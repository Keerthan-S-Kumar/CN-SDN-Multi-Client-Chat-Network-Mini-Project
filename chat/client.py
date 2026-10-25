#!/usr/bin/env python3
"""OWNER: A. CLI chat client; the ChatClient class is also what the D2 bots use.

CLI: python3 chat/client.py --server 10.0.0.1 --user alice [--password pw] [--register | --login]
Commands: /register /login /join <room> /leave <room> /list rooms|users /msg <room> <text> /pm <user> <text>
          /bulk <size> /help /quit
Opens the control connection (5000); after /register or /login it opens 5001 (room chat) and 5002 (private
messages) and sends ATTACH on each (docs/interfaces.md section 3).

Reads commands from stdin, so it also works non-interactively:  printf '/register\\n/join lobby\\n' | python3 chat/client.py ...
When stdin is not a terminal the client waits --linger seconds (default 2) after the last command so replies
and pushed messages can still arrive.
"""
import argparse
import itertools
import socket
import sys
import threading
import time

from chat.protocol import recv_msg, send_msg
from common.constants import PORT_BULK, PORT_CONTROL, PORT_PM, PORT_ROOM


_print_lock = threading.Lock()


def say(text):
    """Print one whole line atomically (reader threads and the main thread both print)."""
    with _print_lock:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()


class ChatError(Exception):
    def __init__(self, code, msg=""):
        super().__init__("%s: %s" % (code, msg))
        self.code, self.msg = code, msg


class ChatClient:
    """Blocking control-port calls plus background readers on the room and private-message connections.

    on_push(frame) is called from a reader thread for every frame that is not the reply to an ATTACH:
    pushed messages ({"type":"MSG",...}) and error replies to asynchronous sends ({"type":"ERR",...}).
    """

    def __init__(self, server, user, password="password", on_push=None, timeout=5.0):
        self.server, self.user, self.password = server, user, password
        self.on_push = on_push or (lambda frame: None)
        self.timeout = timeout
        self.token = None
        self._ids = itertools.count(1)
        self._ctl = socket.create_connection((server, PORT_CONTROL), timeout=timeout)
        self._ctl.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._chat = {}                  # port -> socket
        self._chat_lock = threading.Lock()
        self._closed = False

    # ---- control port --------------------------------------------------------
    def _call(self, type_, **fields):
        rid = next(self._ids)
        send_msg(self._ctl, dict(id=rid, type=type_, **fields))
        while True:
            reply = recv_msg(self._ctl)
            if reply.get("id") == rid:
                break
        if reply.get("type") == "ERR":
            raise ChatError(reply.get("code", "ERR"), reply.get("msg", ""))
        return reply

    def register(self):
        self.token = self._call("REGISTER", user=self.user, password=self.password)["token"]
        self._attach_all()

    def login(self):
        self.token = self._call("LOGIN", user=self.user, password=self.password)["token"]
        self._attach_all()

    def join(self, room):
        return self._call("JOIN", room=room)["members"]

    def leave(self, room):
        self._call("LEAVE", room=room)

    def list(self, what):
        return self._call("LIST", what=what)["items"]

    # ---- chat ports ----------------------------------------------------------
    def _attach_all(self):
        for port in (PORT_ROOM, PORT_PM):
            s = socket.create_connection((self.server, port), timeout=self.timeout)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            send_msg(s, {"id": 1, "type": "ATTACH", "token": self.token})
            reply = recv_msg(s)
            if reply.get("type") != "OK":
                s.close()
                raise ChatError(reply.get("code", "ERR"), reply.get("msg", "attach failed on port %d" % port))
            s.settimeout(None)
            self._chat[port] = s
            threading.Thread(target=self._reader, args=(s,), daemon=True).start()

    def _reader(self, sock):
        try:
            while True:
                frame = recv_msg(sock)
                if frame.get("type") == "OK":
                    continue
                self.on_push(frame)
        except (ConnectionError, OSError, ValueError):
            pass

    def _send_chat(self, port, **fields):
        sock = self._chat.get(port)
        if sock is None:
            raise ChatError("NOT_LOGGED_IN", "register or log in first")
        rid = next(self._ids)
        with self._chat_lock:
            send_msg(sock, dict(id=rid, **fields))
        return rid

    def msg_room(self, room, text, seq=None):
        extra = {} if seq is None else {"seq": seq}
        return self._send_chat(PORT_ROOM, type="MSG_ROOM", room=room, text=text, **extra)

    def msg_pm(self, to, text, seq=None):
        extra = {} if seq is None else {"seq": seq}
        return self._send_chat(PORT_PM, type="MSG_PM", to=to, text=text, **extra)

    def bulk(self, size):
        """Low-priority bulk transfer on port 5003. Returns the number of bytes received."""
        s = socket.create_connection((self.server, PORT_BULK), timeout=self.timeout)
        try:
            send_msg(s, {"id": 1, "type": "ATTACH", "token": self.token})
            reply = recv_msg(s)
            if reply.get("type") != "OK":
                raise ChatError(reply.get("code", "ERR"), reply.get("msg", ""))
            send_msg(s, {"id": 2, "type": "BULK", "size": size})
            reply = recv_msg(s)
            if reply.get("type") != "OK":
                raise ChatError(reply.get("code", "ERR"), reply.get("msg", ""))
            return len(recv_msg(s)["data"])
        finally:
            s.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        for s in list(self._chat.values()) + [self._ctl]:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass


# ---- command line ------------------------------------------------------------
HELP = """commands:
  /register               create the account (--user / --password) and log in
  /login                  log in to an existing account
  /join <room>            join (or create) a room
  /leave <room>           leave a room
  /list rooms|users       show rooms or online users
  /msg <room> <text>      send to a room you have joined
  /pm <user> <text>       private message
  /bulk <size>            bulk transfer on port 5003 (size in bytes)
  /quit                   disconnect"""


def show(frame):
    kind = frame.get("type")
    if kind == "MSG" and frame.get("kind") == "room":
        say("[%s] %s: %s" % (frame.get("room"), frame.get("from"), frame.get("text")))
    elif kind == "MSG" and frame.get("kind") == "pm":
        say("[pm from %s] %s" % (frame.get("from"), frame.get("text")))
    elif kind == "ERR":
        say("! %s: %s" % (frame.get("code"), frame.get("msg", "")))


def run_command(cl, line):
    """Execute one command line. Returns False when the user asked to quit."""
    parts = line.strip().split(None, 2)
    if not parts:
        return True
    cmd = parts[0].lower()
    arg1 = parts[1] if len(parts) > 1 else ""
    rest = parts[2] if len(parts) > 2 else ""
    if cmd == "/quit":
        return False
    if cmd == "/help":
        say(HELP)
    elif cmd == "/register":
        cl.register()
        say("registered and logged in as %s" % cl.user)
    elif cmd == "/login":
        cl.login()
        say("logged in as %s" % cl.user)
    elif cmd == "/join":
        say("joined %s, members: %s" % (arg1, ", ".join(cl.join(arg1))))
    elif cmd == "/leave":
        cl.leave(arg1)
        say("left %s" % arg1)
    elif cmd == "/list":
        say("%s: %s" % (arg1, ", ".join(cl.list(arg1)) or "(none)"))
    elif cmd == "/msg":
        if not arg1 or not rest:
            say("usage: /msg <room> <text>")
        else:
            cl.msg_room(arg1, rest)
    elif cmd == "/pm":
        if not arg1 or not rest:
            say("usage: /pm <user> <text>")
        else:
            cl.msg_pm(arg1, rest)
    elif cmd == "/bulk":
        say("bulk transfer received %d bytes" % cl.bulk(int(arg1)))
    else:
        say("unknown command %r (try /help)" % cmd)
    return True


def main():
    ap = argparse.ArgumentParser(description="Multi-client chat client")
    ap.add_argument("--server", required=True, help="chat server IP, e.g. 10.0.0.1")
    ap.add_argument("--user", required=True)
    ap.add_argument("--password", default="password")
    ap.add_argument("--register", action="store_true", help="run /register at start")
    ap.add_argument("--login", action="store_true", help="run /login at start")
    ap.add_argument("--linger", type=float, default=None,
                    help="seconds to stay connected after stdin ends (default 2 if stdin is not a terminal)")
    args = ap.parse_args()

    try:
        cl = ChatClient(args.server, args.user, args.password, on_push=show)
    except OSError as e:
        print("cannot connect to %s:%d: %s" % (args.server, PORT_CONTROL, e), file=sys.stderr)
        return 1
    interactive = sys.stdin.isatty()
    linger = args.linger if args.linger is not None else (0.0 if interactive else 2.0)
    try:
        try:
            if args.register:
                run_command(cl, "/register")
            elif args.login:
                run_command(cl, "/login")
        except ChatError as e:
            say("! %s: %s" % (e.code, e.msg))
            return 1
        if interactive:
            say("type /help for commands")
        for line in sys.stdin:
            try:
                if not run_command(cl, line):
                    break
            except ChatError as e:
                say("! %s: %s" % (e.code, e.msg))
            except (ConnectionError, OSError) as e:
                say("! connection lost: %s" % e)
                break
            except ValueError as e:
                say("! %s" % e)
        time.sleep(linger)
    except KeyboardInterrupt:
        pass
    finally:
        cl.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
