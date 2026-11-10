#!/usr/bin/env python3
"""OWNER: C. Automatic version of the D2 live demo (docs/demo-d2.md): runs the checks and prints PASS or FAIL for each.

Use it as a rehearsal before the demo, as a fallback if live typing goes wrong, and as a regression check after changes.
It starts the Ryu controller (sdn mode) and the triangle topology itself, so nothing else may be running.

Run from the repo root, as root:
  sudo env PYTHONPATH=. python3 tests/demo_auto.py
  sudo env PYTHONPATH=. python3 tests/demo_auto.py --ryu /home/you/ryu-env/bin/ryu-manager

Checks (about 2 minutes):
  1. pingall has no loss
  2. gate: an unregistered host reaches port 5000 but not port 5001
  3. link failure: the route to h3 on s2 moves to the detour and back, and a 20 s ping loses at most 10 %
  4. congestion: with a 12 Mbps UDP stream, chat latency stays low and chat uses its own queues; meters exist
  5. block: a block event installs 4 priority-400 drop rules
  6. file sharing: a 1 MB file goes through port 5003 under load and arrives intact
Exit status is 0 only if every check passed.
"""
import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "eval"))
from common import constants as C  # noqa: E402

RESULTS = []        # (name, ok, detail)


# ------------------------------------------------------------------ pure helpers (unit-testable) ----
def port_to(a, b):
    """Port on switch a of the triangle link that leads to switch b."""
    for sa, pa, sb, pb in C.TRIANGLE_LINKS:
        if sa == a and sb == b:
            return pa
        if sb == a and sa == b:
            return pb
    raise ValueError("no link %d-%d" % (a, b))


def route_port(flows_text, mac):
    """Output port of the table-1 route for a destination MAC in `ovs-ofctl dump-flows` text, or None."""
    for line in flows_text.splitlines():
        if "dl_dst=%s" % mac in line and "priority=10," in line:
            m = re.search(r"actions=output:(\d+)", line)
            if m:
                return int(m.group(1))
    return None


def queue_stats(text):
    """{queue: (bytes, pkts)} from `ovs-ofctl queue-stats` for the first port listed."""
    out = {}
    for m in re.finditer(r"queue (\d+): bytes=(\d+), pkts=(\d+)", text):
        out.setdefault(int(m.group(1)), (int(m.group(2)), int(m.group(3))))
    return out


def ping_loss(text):
    m = re.search(r"([\d.]+)% packet loss", text)
    return float(m.group(1)) if m else None


def count_block_rules(flows_text, ip):
    return sum(1 for line in flows_text.splitlines() if "priority=400," in line and "nw_src=%s," % ip in line)


# ------------------------------------------------------------------ small utilities ------------------
def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name, ("  (" + detail + ")") if detail else ""), flush=True)
    return ok


def ovs(*args):
    r = subprocess.run(["ovs-ofctl", "-O", "OpenFlow13"] + list(args), capture_output=True, text=True)
    return r.stdout


def wait_for(fn, timeout, step=0.5):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return fn()


def section(title):
    print("\n== %s" % title, flush=True)


# ------------------------------------------------------------------ the demo ---------------------------
def demo(a):
    import run_experiments as R
    from mininet.log import setLogLevel
    from topology import qos
    from topology import topo as T

    setLogLevel("warning")
    os.chdir(ROOT)
    ryu = R.find_ryu(a.ryu)
    if not ryu:
        sys.exit("ryu-manager not found: pass --ryu /path/to/ryu-manager")
    subprocess.run(["mn", "-c"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)   # also kills stale Ryu

    env = dict(os.environ, CONTROLLER_MODE="sdn", PYTHONPATH=ROOT)
    ctl_log = open("/tmp/demo_auto_controller.log", "w")
    ctl = subprocess.Popen([ryu, "controller/app.py"], cwd=ROOT, env=env, stdout=ctl_log,
                           stderr=subprocess.STDOUT, start_new_session=True)
    net = server = iperf_srv = iperf_cli = None
    qos_on = False
    try:
        if not R.wait_rest():
            raise RuntimeError("controller REST did not come up (see /tmp/demo_auto_controller.log)")
        net, root, root_link = T.build(C.TOPO_TRIANGLE)
        net.start()
        root.setIP("%s/%d" % (C.CONTROLLER_MGMT_IP, T.PREFIX), intf=root_link.intf1)
        if not net.waitConnected(timeout=20):
            raise RuntimeError("switches did not connect to the controller")
        qos.setup_qos()
        qos_on = True
        h = {n: net.get(n) for n in ("h1", "h2", "h3", "h4")}

        section("1. Network")
        net.pingAll(timeout=2)                                 # warm-up: learn MACs, install routes
        loss = net.pingAll(timeout=2)
        check("pingall has no loss", loss == 0, "%s%% dropped" % loss)
        h["h1"].cmd("ping -c 1 -W 2 %s" % C.CONTROLLER_MGMT_IP)

        server = h["h1"].popen(["python3", "-u", "chat/server.py", "--host", C.SERVER_IP],
                               stdout=open("/tmp/demo_auto_server.log", "w"), stderr=subprocess.STDOUT)
        time.sleep(1.5)

        section("2. Gate: unauthorized access")
        probe = ("import socket,sys\n"
                 "s=socket.socket(); s.settimeout(3)\n"
                 "try:\n s.connect(('%s',int(sys.argv[1]))); sys.exit(0)\n"
                 "except Exception:\n sys.exit(1)\n" % C.SERVER_IP)
        rc_ctl = h["h4"].popen(["python3", "-c", probe, str(C.PORT_CONTROL)]).wait()
        rc_room = h["h4"].popen(["python3", "-c", probe, str(C.PORT_ROOM)]).wait()
        check("unregistered host reaches control port %d" % C.PORT_CONTROL, rc_ctl == 0)
        check("unregistered host blocked on room port %d" % C.PORT_ROOM, rc_room != 0)

        section("3. Link failure and reroute (s2-s3)")
        mac = C.HOST_MAC["h3"]
        direct, detour = port_to(2, 3), port_to(2, 1)
        ping = h["h2"].popen(["ping", "-c", "100", "-i", "0.2", C.CLIENT_IPS["h3"]],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        before = wait_for(lambda: route_port(ovs("dump-flows", "s2", "table=1"), mac) == direct, 8)
        check("before failure: s2 reaches h3 on the direct port %d" % direct, before)
        net.configLinkStatus("s2", "s3", "down")
        after = wait_for(lambda: route_port(ovs("dump-flows", "s2", "table=1"), mac) == detour, 10)
        check("after link down: s2 reaches h3 via s1 on port %d" % detour, after)
        time.sleep(2)
        net.configLinkStatus("s2", "s3", "up")
        out = ping.communicate(timeout=60)[0].decode(errors="replace")
        pl = ping_loss(out)
        check("ping h2 to h3 lost at most 10%", pl is not None and pl <= 10.0, "%s%% loss" % pl)

        section("4. Congestion: queues and meters")
        srv_cmd, cli_tpl = R.iperf_commands()
        iperf_srv = h["h1"].popen(srv_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)
        iperf_cli = h["h3"].popen([x.replace("{t}", "40") for x in cli_tpl],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3)
        bot = h["h2"].popen(["python3", "-u", "chat/bot.py", "--server", C.SERVER_IP, "--users", "3",
                             "--prefix", "demo", "--duration", "10", "--out", "/tmp/demo_auto.csv"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        bout = bot.communicate(timeout=90)[0].decode(errors="replace")
        m50 = re.search(r"p50=([\d.]+)", bout)
        errs = re.search(r"server errors: (\d+)", bout)
        p50 = float(m50.group(1)) if m50 else None
        check("chat median latency under a 12 Mbps flood is below 50 ms", p50 is not None and p50 < 50,
              "p50 = %s ms" % p50)
        check("bot saw no server errors", errs is not None and int(errs.group(1)) == 0)
        q = queue_stats(ovs("queue-stats", "s1"))
        chat_pkts = sum(q.get(i, (0, 0))[1] for i in (C.Q_HIGH, C.Q_NORMAL, C.Q_BULK))
        flood_bytes = q.get(C.Q_OTHER, (0, 0))[0]
        check("flood travels in queue 0, chat in its own queues", flood_bytes > 1_000_000 and chat_pkts > 0,
              "queue 0 = %d bytes, chat queues = %d packets" % (flood_bytes, chat_pkts))
        meters = ovs("dump-meters", "s1")
        check("throttle and heavy-hitter meters exist",
              "meter=%d" % C.METER_THROTTLE in meters and "meter=%d" % C.METER_HEAVY in meters)

        section("5. Block rules")
        penv = dict(os.environ, PYTHONPATH=ROOT, CONTROLLER_URL="http://127.0.0.1:%d" % C.CONTROLLER_REST_PORT)
        r = subprocess.run([sys.executable, "tools/post_event.py", "block", "", C.CLIENT_IPS["h4"],
                            "--reason", "bad_login"], env=penv, capture_output=True, text=True, cwd=ROOT)
        check("controller accepted the block event", r.stdout.strip() == "ok", r.stdout.strip() or r.stderr.strip()[-80:])
        n = wait_for(lambda: count_block_rules(ovs("dump-flows", "s1", "table=0"), C.CLIENT_IPS["h4"]) == 4, 5)
        check("4 priority-400 drop rules for %s" % C.CLIENT_IPS["h4"], n)

        section("6. File sharing under load")
        ft = h["h2"].popen(["python3", "-u", "eval/file_transfer.py", "--server", C.SERVER_IP, "--mb", "1"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        fout = ft.communicate(timeout=120)[0].decode(errors="replace")
        check("1 MB file uploaded and downloaded intact", ft.returncode == 0 and "intact: yes" in fout,
              fout.strip().splitlines()[-1][:100] if fout.strip() else "no output")
    finally:
        for proc in (iperf_cli, iperf_srv, server):
            R.stop(proc)
        if net is not None:
            try:
                net.stop()
            except Exception:
                pass
        if qos_on:
            try:
                qos.clear_qos()
            except Exception:
                pass
        try:
            os.killpg(os.getpgid(ctl.pid), signal.SIGTERM)
            ctl.wait(timeout=5)
        except Exception:
            try:
                os.killpg(os.getpgid(ctl.pid), signal.SIGKILL)
            except Exception:
                pass
        ctl_log.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ryu", help="path to ryu-manager (default: auto-detect)")
    a = ap.parse_args()
    if os.geteuid() != 0:
        sys.exit("needs root: sudo env PYTHONPATH=. python3 tests/demo_auto.py")
    if shutil.which("ovs-ofctl") is None:
        sys.exit("ovs-ofctl not found: is Open vSwitch installed?")
    try:
        demo(a)
    except Exception as e:           # a crash is a failed demo, not a silent pass
        check("demo ran to the end", False, "%s: %s" % (type(e).__name__, e))
    failed = [r for r in RESULTS if not r[1]]
    print("\n%d checks, %d passed, %d failed" % (len(RESULTS), len(RESULTS) - len(failed), len(failed)))
    for name, _, detail in failed:
        print("  FAILED: %s %s" % (name, detail))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
