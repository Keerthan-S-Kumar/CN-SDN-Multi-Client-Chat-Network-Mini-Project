#!/usr/bin/env python3
"""OWNER: C. A spammer that sends LARGE room messages, for the `floodbig` scenario (eval/run_experiments.py).

chat/bot.py --spammer sends a 4-byte text, so its flood costs the network almost nothing. This one sends --size bytes
per message at --rate messages per second, so the flood actually fills the 10 Mbps bottleneck (200 msg/s x 4000 B
is about 6.4 Mbit/s, before the server fans each message out to the room).
It uses the real client protocol: REGISTER or LOGIN, JOIN, then ATTACH to the room port. The user name ends in `spam`
so the load bots leave it out of their member lists, like the normal spammer.

Run:  python3 eval/bigspam.py --server 10.0.0.1 --prefix h4u --rate 200 --size 4000 --duration 30
Prints how many messages and megabytes it managed to send: when the switch throttles the host, TCP slows it down
and that number drops.
"""
import argparse
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chat.protocol import recv_msg, send_msg  # noqa: E402
from common.constants import MAX_FRAME, PORT_CONTROL, PORT_ROOM, SERVER_IP  # noqa: E402

PASSWORD = "pw123"


def call(sock, type_, ident, **fields):
    send_msg(sock, {"id": ident, "type": type_, **fields})
    return recv_msg(sock)


def connect(server, name, room):
    ctl = socket.create_connection((server, PORT_CONTROL), timeout=5)
    ctl.settimeout(None)
    r = call(ctl, "REGISTER", 1, user=name, password=PASSWORD)
    if r.get("type") == "ERR" and r.get("code") == "USER_EXISTS":
        r = call(ctl, "LOGIN", 2, user=name, password=PASSWORD)
    if r.get("type") == "ERR":
        raise RuntimeError(r.get("code"))
    token = r["token"]
    r = call(ctl, "JOIN", 3, room=room)
    if r.get("type") == "ERR":
        raise RuntimeError(r.get("code"))
    rs = socket.create_connection((server, PORT_ROOM), timeout=5)
    rs.settimeout(None)
    r = call(rs, "ATTACH", 4, token=token)
    if r.get("type") == "ERR":
        raise RuntimeError(r.get("code"))
    return ctl, rs


def drain(sock):
    """Read and drop everything the server sends back, so the socket never fills up."""
    try:
        while True:
            recv_msg(sock)
    except (OSError, ValueError):
        pass


def make_text(size):
    head = "SPAM|"
    return head + "x" * max(0, size - len(head))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--server", default=SERVER_IP)
    ap.add_argument("--prefix", default="h4u")
    ap.add_argument("--room", default="general")
    ap.add_argument("--rate", type=float, default=200.0, help="messages per second")
    ap.add_argument("--size", type=int, default=4000, help="message text bytes")
    ap.add_argument("--duration", type=float, default=30.0)
    ap.add_argument("--warmup", type=float, default=3.0, help="seconds to wait so the other bots can join first")
    ap.add_argument("--drain", type=float, default=3.0, help="unused; accepted so it can share the bot's flags")
    a = ap.parse_args()
    if not 0 < a.size < MAX_FRAME - 1024:
        sys.exit("size must be between 1 and %d bytes" % (MAX_FRAME - 1024))

    name = a.prefix + "spam"
    try:
        ctl, rs = connect(a.server, name, a.room)
    except (OSError, RuntimeError, ValueError) as e:
        sys.exit("bigspam %s could not connect: %s" % (name, e))
    threading.Thread(target=drain, args=(rs,), daemon=True).start()
    threading.Thread(target=drain, args=(ctl,), daemon=True).start()
    print("big spammer %s connected" % name, flush=True)
    time.sleep(a.warmup)

    text = make_text(a.size)
    interval = 1.0 / a.rate
    start = time.time() + 0.5
    end = start + a.duration
    t, seq, sent_bytes = start, 0, 0
    print("sending for %.0f s ..." % a.duration, flush=True)
    try:
        while t < end:
            delay = t - time.time()
            if delay > 0:
                time.sleep(delay)
            send_msg(rs, {"id": 100 + seq, "type": "MSG_ROOM", "room": a.room, "text": text, "seq": seq})
            seq += 1
            sent_bytes += len(text)
            t += interval
    except OSError:
        pass
    elapsed = max(0.001, time.time() - start)
    print("big spammer sent %d messages (%.2f MB) in %.1f s = %.2f Mbit/s" % (
        seq, sent_bytes / 1e6, elapsed, sent_bytes * 8 / 1e6 / elapsed), flush=True)
    for s in (rs, ctl):
        try:
            s.close()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
