#!/usr/bin/env python3
"""OWNER: C. Experiment runner for docs/eval-plan.md.

For every mode x scenario x run it:
  1. cleans old Mininet state, starts the Ryu controller with CONTROLLER_MODE=<mode>
  2. builds the triangle topology + QoS queues, starts the chat server on h1
  3. runs chat/bot.py on h2/h3/h4 (plus iperf or a link failure, depending on the scenario)
  4. writes   eval/results/<mode>/<scenario>/run<k>.csv    (all bot rows, merged)
              eval/results/<mode>/<scenario>/run<k>.jsonl  (controller events + the runner's own events)
     which eval/analyze.py and eval/plot.py read.

Run from the repo root, as root, with the Ryu environment installed:
  sudo env PYTHONPATH=. python3 eval/run_experiments.py --core --runs 3
  sudo env PYTHONPATH=. python3 eval/run_experiments.py --only idle --mode sdn --runs 1 --duration 10   (smoke test)
  sudo env PYTHONPATH=. python3 eval/run_experiments.py --dry-run                                       (print the plan)
Then:  make report
Each run is a fresh child process (--one), so a crash in one run cannot poison the next.
"""
import argparse
import csv
import glob
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from common import constants as C  # noqa: E402

CORE = ("idle", "congested", "flood", "linkfail")
ROOM = "general"
SCALE_RATE = {5: 2.0, 10: 2.0, 25: 2.0, 50: 1.0, 100: 0.5}   # msgs/s per bot; lower for the big runs so the VM keeps up
IPERF_PORT, IPERF_MBPS = 5201, 12
LINK_DOWN_AT, LINK_UP_AT = 10.0, 20.0                          # seconds after traffic starts (docs/eval-plan.md)
DRAIN, WARMUP = 3.0, 3.0
HOSTS = ("h2", "h3", "h4")


# ------------------------------------------------------------------ plan (pure, testable) ----
def split(n, hosts=HOSTS):
    base, extra = divmod(n, len(hosts))
    return {h: base + (1 if i < extra else 0) for i, h in enumerate(hosts)}


def make_plan(scenario, users=10, rate=None):
    """-> {"bots": {host: (n_users, spammer)}, "rate": msgs/s, "iperf": bool, "linkfail": bool}"""
    if scenario not in C.SCENARIOS:
        raise ValueError("unknown scenario %r" % scenario)
    plan = {"rate": rate if rate is not None else 2.0, "iperf": False, "linkfail": False}
    if scenario.startswith("scale"):
        n = int(scenario[5:])
        if rate is None:
            plan["rate"] = SCALE_RATE.get(n, 2.0)
        plan["bots"] = {h: (k, False) for h, k in split(n).items()}
    elif scenario == "flood":
        # the spammer is alone on h4: rules are per IP, so flagging it throttles the whole host
        plan["bots"] = {"h2": ((users + 1) // 2, False), "h3": (users // 2, False), "h4": (0, True)}
    else:
        plan["bots"] = {h: (k, False) for h, k in split(users).items()}
        plan["iperf"] = scenario == "congested"
        plan["linkfail"] = scenario == "linkfail"
    return plan


def merge_run(stem, parts_dir):
    """Concatenate the per-host bot CSVs into <stem>.csv. Returns the number of data rows."""
    header = ["t_send", "t_recv", "sender", "receiver", "kind", "room", "bytes"]
    rows = []
    for p in sorted(glob.glob(os.path.join(parts_dir, "*.csv"))):
        with open(p, newline="") as f:
            r = csv.reader(f)
            next(r, None)
            rows.extend(r)
    rows.sort(key=lambda x: float(x[0]))
    with open(stem + ".csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return len(rows)


def write_events(stem, controller_log, runner_events):
    """<stem>.jsonl = controller events + runner events, sorted by time (the format analyze.py expects)."""
    events = []
    if os.path.exists(controller_log):
        with open(controller_log) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        pass
    events.extend({"t": t, "kind": k, "detail": d} for t, k, d in runner_events)
    events.sort(key=lambda e: e.get("t", 0))
    with open(stem + ".jsonl", "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    return len(events)


# ------------------------------------------------------------------ helpers -----------------
def find_ryu(explicit=None):
    cands = [explicit, os.environ.get("RYU")]
    homes = []
    if os.environ.get("SUDO_USER"):
        homes.append(os.path.expanduser("~" + os.environ["SUDO_USER"]))
    homes.append(os.path.expanduser("~"))
    for h in homes:
        for d in ("ryu-env", "ryuenv"):
            cands.append(os.path.join(h, d, "bin", "ryu-manager"))
    cands.append(shutil.which("ryu-manager"))
    for c in cands:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def wait_rest(timeout=25.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/health" % C.CONTROLLER_REST_PORT, timeout=1).read()
            return True
        except Exception:
            time.sleep(0.5)
    return False


def iperf_commands():
    """-> (server_cmd, client_cmd_template) for whichever iperf is installed."""
    if shutil.which("iperf"):
        return (["iperf", "-s", "-u", "-p", str(IPERF_PORT)],
                ["iperf", "-c", C.SERVER_IP, "-u", "-b", "%dM" % IPERF_MBPS, "-p", str(IPERF_PORT), "-t", "{t}"])
    if shutil.which("iperf3"):
        return (["iperf3", "-s", "-p", str(IPERF_PORT)],
                ["iperf3", "-c", C.SERVER_IP, "-u", "-b", "%dM" % IPERF_MBPS, "-p", str(IPERF_PORT), "-t", "{t}"])
    raise SystemExit("iperf is not installed: run  sudo apt install -y iperf")


def stop(proc):
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


# ------------------------------------------------------------------ one run (needs root + Mininet) --
def run_one(mode, scenario, k, a):
    from mininet.log import setLogLevel
    from topology import qos
    from topology import topo as T

    setLogLevel("warning")
    os.chdir(ROOT)
    plan = make_plan(scenario, a.users, a.rate)
    outdir = os.path.join(ROOT, C.RESULTS_DIR, mode, scenario)
    os.makedirs(outdir, exist_ok=True)
    stem = os.path.join(outdir, "run%d" % k)
    parts = stem + "_parts"
    shutil.rmtree(parts, ignore_errors=True)
    os.makedirs(parts)
    controller_log = os.path.join(ROOT, C.CONTROLLER_LOG)
    if os.path.exists(controller_log):
        os.remove(controller_log)

    ryu = find_ryu(a.ryu)
    if not ryu:
        raise SystemExit("ryu-manager not found: pass --ryu /path/to/ryu-manager")

    subprocess.run(["mn", "-c"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)   # also kills stale Ryu
    env = dict(os.environ, CONTROLLER_MODE=mode, PYTHONPATH=ROOT)
    ctl_log = open(stem + "_controller.log", "w")
    ctl = subprocess.Popen([ryu, "controller/app.py"], cwd=ROOT, env=env, stdout=ctl_log,
                           stderr=subprocess.STDOUT, start_new_session=True)
    runner_events = []
    net = server = iperf_srv = iperf_cli = None
    bot_procs = []
    qos_on = False
    try:
        if not wait_rest():
            raise RuntimeError("controller REST did not come up (see %s_controller.log)" % stem)
        net, root, root_link = T.build(C.TOPO_TRIANGLE)
        net.start()
        root.setIP("%s/%d" % (C.CONTROLLER_MGMT_IP, T.PREFIX), intf=root_link.intf1)
        if not net.waitConnected(timeout=20):
            raise RuntimeError("switches did not connect to the controller")
        qos.setup_qos()
        qos_on = True
        h = {n: net.get(n) for n in ("h1", "h2", "h3", "h4")}
        net.pingAll(timeout=2)                                           # learn MACs / install routes
        h["h1"].cmd("ping -c 1 -W 2 %s" % C.CONTROLLER_MGMT_IP)         # server -> controller REST path

        server = h["h1"].popen(["python3", "chat/server.py", "--host", C.SERVER_IP],
                               stdout=open(stem + "_server.log", "w"), stderr=subprocess.STDOUT)
        if plan["iperf"]:
            srv_cmd, cli_tpl = iperf_commands()
            iperf_srv = h["h1"].popen(srv_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)

        started = threading.Event()
        t_start = [None]
        logs = {}

        def reader(name, proc):
            for raw in iter(proc.stdout.readline, b""):
                line = raw.decode(errors="replace")
                logs[name].write(line)
                logs[name].flush()
                if "sending for" in line and not started.is_set():
                    t_start[0] = time.time()
                    started.set()

        for hname, (n, spam) in plan["bots"].items():
            cmd = ["python3", "-u", "chat/bot.py", "--server", C.SERVER_IP, "--users", str(n),
                   "--prefix", hname + "u", "--room", ROOM, "--rate", str(plan["rate"]),
                   "--duration", str(a.duration), "--warmup", str(WARMUP), "--drain", str(DRAIN),
                   "--out", os.path.join(parts, hname + ".csv")]
            if spam:
                cmd.append("--spammer")
            proc = h[hname].popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            logs[hname] = open("%s_bots_%s.log" % (stem, hname), "w")
            threading.Thread(target=reader, args=(hname, proc), daemon=True).start()
            bot_procs.append(proc)

        if not started.wait(timeout=60):
            t_start[0] = time.time()
        t0 = t_start[0]
        runner_events.append((t0, "traffic_start", {"scenario": scenario, "mode": mode, "run": k}))

        if plan["iperf"]:
            cmd = [x.replace("{t}", str(int(a.duration))) for x in cli_tpl]
            iperf_cli = h["h3"].popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            runner_events.append((time.time(), "iperf_start", {"mbps": IPERF_MBPS, "src": "h3", "dst": "h1"}))

        if plan["linkfail"]:
            time.sleep(max(0.0, t0 + LINK_DOWN_AT - time.time()))
            net.configLinkStatus("s1", "s2", "down")
            runner_events.append((time.time(), "link_down_cmd", {"link": "s1-s2"}))
            time.sleep(max(0.0, t0 + LINK_UP_AT - time.time()))
            net.configLinkStatus("s1", "s2", "up")
            runner_events.append((time.time(), "link_up_cmd", {"link": "s1-s2"}))

        deadline = time.time() + a.duration + WARMUP + DRAIN + 40
        for proc in bot_procs:
            try:
                proc.wait(timeout=max(1.0, deadline - time.time()))
            except subprocess.TimeoutExpired:
                runner_events.append((time.time(), "bot_timeout", {}))
                stop(proc)
        runner_events.append((time.time(), "traffic_end", {}))
    finally:
        for proc in bot_procs + [iperf_cli, iperf_srv, server]:
            stop(proc)
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

    rows = merge_run(stem, parts)
    n_events = write_events(stem, controller_log, runner_events)
    with open(stem + ".meta.json", "w") as f:
        json.dump({"mode": mode, "scenario": scenario, "run": k, "users": a.users, "rate": plan["rate"],
                   "duration": a.duration, "bots": plan["bots"], "rows": rows, "events": n_events}, f, indent=1)
    if not a.keep_parts:
        shutil.rmtree(parts, ignore_errors=True)
    print("  wrote %s.csv (%d rows) and %s.jsonl (%d events)" % (stem, rows, stem, n_events))
    return 0 if rows > 0 else 2


# ------------------------------------------------------------------ orchestrator ------------
def main():
    ap = argparse.ArgumentParser(description="Run the D2 experiments (docs/eval-plan.md)")
    ap.add_argument("--only", help="comma separated scenarios (default: all of %s)" % ",".join(C.SCENARIOS))
    ap.add_argument("--core", action="store_true", help="only idle,congested,flood,linkfail")
    ap.add_argument("--mode", choices=C.MODES, help="only this mode (default: both)")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--duration", type=float, default=30.0, help="seconds of traffic per run")
    ap.add_argument("--users", type=int, default=10, help="bots for idle/congested/flood/linkfail")
    ap.add_argument("--rate", type=float, default=None, help="msgs/s per bot (default 2; lower for scale50/100)")
    ap.add_argument("--quick", action="store_true", help="1 run of 10 s each, for a first check")
    ap.add_argument("--ryu", help="path to ryu-manager (default: auto-detect)")
    ap.add_argument("--keep-parts", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    ap.add_argument("--one", nargs=3, metavar=("MODE", "SCENARIO", "RUN"), help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.quick:
        a.runs, a.duration = 1, 10.0

    if a.one:
        if os.geteuid() != 0:
            sys.exit("run as root: sudo env PYTHONPATH=. python3 eval/run_experiments.py ...")
        return run_one(a.one[0], a.one[1], int(a.one[2]), a)

    scenarios = list(CORE) if a.core else (a.only.split(",") if a.only else list(C.SCENARIOS))
    for s in scenarios:
        if s not in C.SCENARIOS:
            sys.exit("unknown scenario %r (choose from %s)" % (s, ", ".join(C.SCENARIOS)))
    modes = [a.mode] if a.mode else list(C.MODES)
    jobs = [(m, s, k) for m in modes for s in scenarios for k in range(1, a.runs + 1)]
    est = len(jobs) * (a.duration + 35) / 60
    print("%d runs, about %.0f minutes" % (len(jobs), est))

    if a.dry_run:
        for m, s, k in jobs:
            p = make_plan(s, a.users, a.rate)
            print("  %-8s %-10s run%d  rate=%.1f/s  bots=%s%s%s" % (
                m, s, k, p["rate"], p["bots"], "  +iperf" if p["iperf"] else "",
                "  +linkfail" if p["linkfail"] else ""))
        return 0
    if os.geteuid() != 0:
        sys.exit("run as root: sudo env PYTHONPATH=. python3 eval/run_experiments.py ...")

    env = dict(os.environ, PYTHONPATH=ROOT)
    failed = []
    for i, (m, s, k) in enumerate(jobs, 1):
        print("[%d/%d] %s %s run%d" % (i, len(jobs), m, s, k), flush=True)
        cmd = [sys.executable, os.path.abspath(__file__), "--one", m, s, str(k),
               "--duration", str(a.duration), "--users", str(a.users)]
        if a.rate is not None:
            cmd += ["--rate", str(a.rate)]
        if a.ryu:
            cmd += ["--ryu", a.ryu]
        if a.keep_parts:
            cmd.append("--keep-parts")
        try:
            rc = subprocess.run(cmd, cwd=ROOT, env=env, timeout=a.duration + 180).returncode
        except subprocess.TimeoutExpired:
            rc = -1
            subprocess.run(["mn", "-c"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if rc != 0:
            failed.append((m, s, k, rc))
            print("  FAILED (exit %s) - see eval/results/%s/%s/run%d_*.log" % (rc, m, s, k))
    subprocess.run(["mn", "-c"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("\nfinished: %d ok, %d failed" % (len(jobs) - len(failed), len(failed)))
    for m, s, k, rc in failed:
        print("  failed:", m, s, "run%d" % k)
    print("next: make report   (writes eval/results/summary.csv and the graphs)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
