"""OWNER: A. Load generator (D2). CLI in docs/interfaces.md §6.
N bots register/login, join --room, send room messages at --rate msg/s for --duration s.
Each message text embeds a unique seq; receivers log arrival. Writes CSV per §11:
  t_send,t_recv,sender,receiver,kind,room,bytes   (empty t_recv = lost)
Flag --spammer sends 200 msg/s. Bots only talk through the real client protocol."""
import argparse
import csv
import os
import random
import re
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chat.protocol import send_msg, recv_msg  # noqa: E402
from common.constants import PORT_CONTROL, PORT_ROOM, SERVER_IP  # noqa: E402

PASSWORD = "pw123"
SPAM_RATE = 200.0
NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")


class Bot:
    def __init__(self, name, args, spammer=False):
        self.name = name
        self.args = args
        self.spammer = spammer
        self.ok = False
        self.ctl = None
        self.room_sock = None
        self.next_id = 1
        self.members = []      # expected receivers of my messages
        self.sent = []         # (seq, t_send, nbytes, receivers)
        self.recv = []         # (sender, seq, t_send, t_recv, nbytes)
        self.errors = 0

    def call(self, sock, type_, **fields):
        send_msg(sock, {"id": self.next_id, "type": type_, **fields})
        self.next_id += 1
        return recv_msg(sock)

    def setup(self):
        a = self.args
        try:
            self.ctl = socket.create_connection((a.server, PORT_CONTROL), timeout=5)
            self.ctl.settimeout(None)
            r = self.call(self.ctl, "REGISTER", user=self.name, password=PASSWORD)
            if r["type"] == "ERR" and r["code"] == "USER_EXISTS":
                r = self.call(self.ctl, "LOGIN", user=self.name, password=PASSWORD)
            if r["type"] == "ERR":
                raise RuntimeError(r["code"])
            token = r["token"]
            r = self.call(self.ctl, "JOIN", room=a.room)
            if r["type"] == "ERR":
                raise RuntimeError(r["code"])
            self.room_sock = socket.create_connection((a.server, PORT_ROOM), timeout=5)
            self.room_sock.settimeout(None)
            r = self.call(self.room_sock, "ATTACH", token=token)
            if r["type"] == "ERR":
                raise RuntimeError(r["code"])
            self.ok = True
        except (OSError, RuntimeError, ValueError) as e:
            print(f"bot {self.name} setup failed: {e}")

    def refresh_members(self):
        """JOIN again (harmless) to learn who is in the room now."""
        try:
            r = self.call(self.ctl, "JOIN", room=self.args.room)
            self.members = [m for m in r.get("members", [])
                            if m != self.name and not m.endswith("spam")]
        except (OSError, ValueError):
            pass

    def receiver(self):
        try:
            while True:
                m = recv_msg(self.room_sock)
                now = time.time()
                if m.get("type") == "MSG" and m.get("kind") == "room":
                    if self.spammer:
                        continue
                    text = m.get("text", "")
                    p = text.split("|", 4)
                    if len(p) >= 4 and p[0] == "BOT":
                        try:
                            self.recv.append((p[1], int(p[2]), float(p[3]),
                                              now, len(text.encode())))
                        except ValueError:
                            pass
                elif m.get("type") == "ERR":
                    self.errors += 1
        except (OSError, ValueError):
            pass

    def sender(self, start_at, end_at):
        a = self.args
        interval = 1.0 / (SPAM_RATE if self.spammer else a.rate)
        t = start_at + random.random() * interval    # spread the bots out
        seq = 0
        try:
            while t < end_at:
                delay = t - time.time()
                if delay > 0:
                    time.sleep(delay)
                if self.spammer:
                    req = {"id": self.next_id, "type": "MSG_ROOM",
                           "room": a.room, "text": "SPAM"}
                else:
                    t_send = float(f"{time.time():.6f}")
                    text = f"BOT|{self.name}|{seq}|{t_send:.6f}|"
                    text += "x" * max(0, a.size - len(text))
                    req = {"id": self.next_id, "type": "MSG_ROOM",
                           "room": a.room, "text": text, "seq": seq}
                    self.sent.append((seq, t_send, len(text.encode()), self.members))
                self.next_id += 1
                send_msg(self.room_sock, req)
                seq += 1
                t += interval
        except OSError:
            pass

    def close(self):
        for s in (self.room_sock, self.ctl):
            if s:
                try:
                    s.close()
                except OSError:
                    pass


def parallel(fn, items):
    threads = [threading.Thread(target=fn, args=(i,)) for i in items]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def build_rows(bots, room):
    got = set()
    rows = []
    for b in bots:
        for sender, seq, t_send, t_recv, nbytes in b.recv:
            got.add((sender, b.name, seq))
            rows.append((t_send, t_recv, sender, b.name, "room", room, nbytes))
    for b in bots:
        for seq, t_send, nbytes, receivers in b.sent:
            for r in receivers:
                if (b.name, r, seq) not in got:
                    rows.append((t_send, "", b.name, r, "room", room, nbytes))
    rows.sort(key=lambda r: r[0])
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default=SERVER_IP)
    ap.add_argument("--users", type=int, default=10)
    ap.add_argument("--prefix", default="bot")
    ap.add_argument("--room", default="general")
    ap.add_argument("--rate", type=float, default=2.0, help="messages/s per bot")
    ap.add_argument("--duration", type=float, default=30.0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--size", type=int, default=64, help="message text bytes")
    ap.add_argument("--warmup", type=float, default=3.0,
                    help="seconds to wait so bots on other hosts can join")
    ap.add_argument("--drain", type=float, default=3.0,
                    help="seconds to wait for late messages at the end")
    ap.add_argument("--spammer", action="store_true",
                    help="add one extra bot sending 200 msg/s")
    a = ap.parse_args()

    names = [f"{a.prefix}{i}" for i in range(1, a.users + 1)]
    bots = [Bot(n, a) for n in names]
    if a.spammer:
        bots.append(Bot(f"{a.prefix}spam", a, spammer=True))
    for b in bots:
        if not NAME_RE.match(b.name):
            sys.exit(f"bad bot name {b.name!r}: use a shorter --prefix")

    parallel(lambda b: b.setup(), bots)
    bots = [b for b in bots if b.ok]
    if not bots:
        sys.exit("no bots could connect")
    print(f"{len(bots)} bots connected")

    for b in bots:
        threading.Thread(target=b.receiver, daemon=True).start()
    time.sleep(a.warmup)
    parallel(lambda b: b.refresh_members(), [b for b in bots if not b.spammer])

    start_at = time.time() + 0.5
    end_at = start_at + a.duration
    for b in bots:
        threading.Thread(target=b.sender, args=(start_at, end_at), daemon=True).start()
    print(f"sending for {a.duration:.0f} s ...")
    time.sleep(max(0, end_at + a.drain - time.time()))

    for b in bots:
        b.close()

    rows = build_rows([b for b in bots if not b.spammer], a.room)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_send", "t_recv", "sender", "receiver", "kind", "room", "bytes"])
        for t_send, t_recv, s, r, k, room, n in rows:
            w.writerow([f"{t_send:.6f}", f"{t_recv:.6f}" if t_recv != "" else "",
                        s, r, k, room, n])

    sent = sum(len(b.sent) for b in bots)
    delivered = [r for r in rows if r[1] != ""]
    lat = sorted((r[1] - r[0]) * 1000 for r in delivered)
    print(f"messages sent: {sent}, rows: {len(rows)}, delivered: {len(delivered)}, "
          f"server errors: {sum(b.errors for b in bots)}")
    if lat:
        print(f"latency ms: p50={lat[len(lat) // 2]:.2f} "
              f"p95={lat[int(len(lat) * 0.95)]:.2f} max={lat[-1]:.2f}")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()