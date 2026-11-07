"""Chat client (Part A). Implements docs/interfaces.md sections 2-3.

CLI: python3 chat/client.py --server 10.0.0.1 --user alice
Commands: /register /login /join <room> /leave <room> /list rooms|users
          /msg <room> <text> /pm <user> <text> /sendroom <room> <file> /sendpm <user> <file>
          /get <file id> [folder] /quit
Opens 5000 (control), then 5001, 5002 and 5003 (files) after login using ATTACH.
"""
import argparse
import base64
import os
import socket
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chat.protocol import send_msg, recv_msg  # noqa: E402
from common.constants import FILE_CHUNK, MAX_FILE, PORT_BULK, PORT_CONTROL, PORT_ROOM, PORT_PM, SERVER_IP  # noqa: E402

HELP = """commands:
  /register <user> <password>   create an account and log in
  /login <user> <password>      log in to an existing account
  /join <room>                  join (or create) a room; it becomes your current room
  /leave <room>                 leave a room
  /room <room>                  switch your current room (must be joined)
  /msg <room> <text>            send a message to a specific room
  /pm <user> <text>             private message
  /sendroom <room> <file>       share a file with a room (up to 5 MB)
  /sendpm <user> <file>         share a file privately
  /get <file id> [folder]       download a shared file (the id is shown with the notice)
  /list users|rooms             show who is online / which rooms exist
  /help                         show this help
  /quit                         exit
anything else you type is sent to your current room"""


class Client:
    def __init__(self, server):
        self.server = server
        self.user = None
        self.token = None
        self.next_id = 1
        self.room = None
        self.running = True
        self.ctl = None
        self.room_sock = None
        self.pm_sock = None
        self.bulk_sock = None
        self.bulk_lock = threading.Lock()

    def rid(self):
        n = self.next_id
        self.next_id += 1
        return n

    def call(self, sock, type_, **fields):
        """Send a request and wait for its reply (only on sockets with no listener thread)."""
        send_msg(sock, {"id": self.rid(), "type": type_, **fields})
        return recv_msg(sock)

    def send_only(self, sock, type_, **fields):
        """Send a request; the listener thread prints any error reply."""
        send_msg(sock, {"id": self.rid(), "type": type_, **fields})

    # ---- connection and login ----
    def open_control(self):
        self.ctl = socket.create_connection((self.server, PORT_CONTROL), timeout=5)
        self.ctl.settimeout(None)

    def authenticate(self, type_, user, password):
        """REGISTER or LOGIN on 5000, then ATTACH on 5001 and 5002."""
        r = self.call(self.ctl, type_, user=user, password=password)
        if r["type"] == "ERR":
            print(f"! {r['code']}: {r.get('msg', '')}")
            return False
        self.user, self.token = user, r["token"]
        if not self.attach_all():
            return False
        print(f"logged in as {user}")
        return True

    def attach_all(self):
        """ATTACH on 5001 and 5002 (with listener threads) and on 5003 (request and reply, for files)."""
        socks = []
        for port in (PORT_ROOM, PORT_PM, PORT_BULK):
            s = socket.create_connection((self.server, port), timeout=5)
            s.settimeout(None)
            a = self.call(s, "ATTACH", token=self.token)
            if a["type"] == "ERR":
                print(f"! attach failed on {port}: {a['code']}")
                return False
            socks.append(s)
        self.room_sock, self.pm_sock, self.bulk_sock = socks
        for s in socks[:2]:
            threading.Thread(target=self.listen, args=(s,), daemon=True).start()
        return True

    def auto_login(self, user, password):
        """Used by --user: try REGISTER, fall back to LOGIN if the name exists."""
        r = self.call(self.ctl, "REGISTER", user=user, password=password)
        if r["type"] == "ERR" and r["code"] == "USER_EXISTS":
            return self.authenticate("LOGIN", user, password)
        if r["type"] == "ERR":
            print(f"! {r['code']}: {r.get('msg', '')}")
            return False
        # REGISTER succeeded and already logged us in on this connection;
        # we still need ATTACH, so reuse the token
        self.user, self.token = user, r["token"]
        if not self.attach_all():
            return False
        print(f"logged in as {user}")
        return True

    # ---- incoming messages (background threads) ----
    def listen(self, sock):
        try:
            while self.running:
                m = recv_msg(sock)
                if m.get("type") == "MSG":
                    tail = f"  -> /get {m['file']['fid']}" if m.get("file") else ""
                    if m.get("kind") == "room":
                        print(f"\n[{m['room']}] {m['from']}: {m['text']}{tail}")
                    else:
                        print(f"\n[PM from {m['from']}] {m['text']}{tail}")
                elif m.get("type") == "ERR":
                    print(f"\n! {m['code']}: {m.get('msg', '')}")
                if m.get("type") in ("MSG", "ERR"):
                    print("> ", end="", flush=True)                
        except (ConnectionError, OSError, ValueError):
            if self.running:
                print("\n[connection to server closed]")
                self.running = False

    # ---- commands ----
    def need_login(self):
        if self.token:
            return False
        print("not logged in: use /register <user> <password> or /login <user> <password>")
        return True

    def say(self, text):
        if self.need_login():
            return
        if not self.room:
            print("join a room first: /join <room>")
            return
        self.send_only(self.room_sock, "MSG_ROOM", room=self.room, text=text)
        print(f"[{self.room}] me: {text}")

    # ---- files (port 5003) ----
    def send_file(self, path, **target):
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as f:
                data = f.read(MAX_FILE + 1)
        except OSError as e:
            print(f"! cannot read {path}: {e}")
            return
        if not 0 < len(data) <= MAX_FILE:
            print(f"! a file must be 1 byte to {MAX_FILE // (1024 * 1024)} MB")
            return
        with self.bulk_lock:
            r = self.call(self.bulk_sock, "FILE_UP", name=os.path.basename(path), size=size, **target)
            if r["type"] == "ERR":
                print(f"! {r['code']}: {r.get('msg', '')}")
                return
            parts = [data[i:i + FILE_CHUNK] for i in range(0, len(data), FILE_CHUNK)]
            for i, part in enumerate(parts):
                send_msg(self.bulk_sock, {"id": self.rid(), "type": "FILE_CHUNK", "fid": r["fid"], "seq": i,
                                          "data": base64.b64encode(part).decode(), "last": i == len(parts) - 1})
            done = recv_msg(self.bulk_sock)
        if done["type"] == "OK":
            print(f"sent {done['name']} ({done['size']} bytes), file id {done['fid']}")
        else:
            print(f"! {done['code']}: {done.get('msg', '')}")

    def get_file(self, fid, folder="."):
        with self.bulk_lock:
            r = self.call(self.bulk_sock, "FILE_GET", fid=fid)
            if r["type"] == "ERR":
                print(f"! {r['code']}: {r.get('msg', '')}")
                return
            data = b"".join(base64.b64decode(recv_msg(self.bulk_sock)["data"]) for _ in range(r["chunks"]))
        out = os.path.join(folder, r["name"])
        try:
            with open(out, "wb") as f:
                f.write(data)
        except OSError as e:
            print(f"! cannot write {out}: {e}")
            return
        print(f"saved {out} ({len(data)} bytes)")

    def command(self, line):
        """Return False to quit."""
        parts = line.split(None, 2)
        cmd = parts[0]

        if cmd == "/quit":
            return False
        if cmd == "/help":
            print(HELP)
        elif cmd in ("/register", "/login") and len(parts) == 3:
            if self.token:
                print("already logged in")
            else:
                self.authenticate("REGISTER" if cmd == "/register" else "LOGIN",
                                  parts[1], parts[2])
        elif cmd in ("/register", "/login"):
            print(f"usage: {cmd} <user> <password>")
        elif self.need_login():
            pass
        elif cmd == "/join" and len(parts) >= 2:
            r = self.call(self.ctl, "JOIN", room=parts[1])
            if r["type"] == "OK":
                self.room = parts[1]
                print(f"joined {parts[1]}; members: {', '.join(r['members'])}")
            else:
                print(f"! {r['code']}: {r.get('msg', '')}")
        elif cmd == "/leave" and len(parts) >= 2:
            r = self.call(self.ctl, "LEAVE", room=parts[1])
            if r["type"] == "OK":
                print(f"left {parts[1]}")
                if self.room == parts[1]:
                    self.room = None
            else:
                print(f"! {r['code']}: {r.get('msg', '')}")
        elif cmd == "/room" and len(parts) >= 2:
            self.room = parts[1]
            print(f"current room: {self.room}")
        elif cmd == "/msg" and len(parts) >= 3:
            self.send_only(self.room_sock, "MSG_ROOM", room=parts[1], text=parts[2])
            print(f"[{parts[1]}] me: {parts[2]}")
        elif cmd == "/pm" and len(parts) >= 3:
            self.send_only(self.pm_sock, "MSG_PM", to=parts[1], text=parts[2])
            print(f"[PM to {parts[1]}] {parts[2]}")
        elif cmd in ("/sendroom", "/sendpm") and len(parts) >= 3:
            self.send_file(parts[2], **({"room": parts[1]} if cmd == "/sendroom" else {"to": parts[1]}))
        elif cmd == "/get" and len(parts) >= 2:
            self.get_file(parts[1], parts[2] if len(parts) > 2 else ".")
        elif cmd == "/list" and len(parts) >= 2:
            r = self.call(self.ctl, "LIST", what=parts[1])
            if r["type"] == "OK":
                print(", ".join(r["items"]) or "(none)")
            else:
                print(f"! {r['code']}: {r.get('msg', '')}")
        else:
            print("unknown or incomplete command, try /help")
        return True

    def run(self):
        print("connected. type /help for commands")
        while self.running:
            try:
                line = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            try:
                if line.startswith("/"):
                    if not self.command(line):
                        break
                else:
                    self.say(line)
            except (ConnectionError, OSError):
                print("[connection to server lost]")
                break
        self.close()

    def close(self):
        self.running = False
        for s in (self.ctl, self.room_sock, self.pm_sock, self.bulk_sock):
            if s:
                try:
                    s.close()
                except OSError:
                    pass
        print("bye")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default=SERVER_IP)
    ap.add_argument("--user", help="log in automatically (registers if the name is new)")
    ap.add_argument("--password", default="pw123")
    args = ap.parse_args()
    client = Client(args.server)
    try:
        client.open_control()
        if args.user and not client.auto_login(args.user, args.password):
            sys.exit(1)
        client.run()
    except (ConnectionError, OSError) as e:
        print(f"could not connect to {args.server}: {e}")