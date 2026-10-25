"""Chat server (Part A). Implements docs/interfaces.md sections 2-4.
Ports: 5000 control, 5001 room chat, 5002 private messages.
"""
import argparse
import hashlib
import os
import re
import secrets
import socket
import sys
import threading
import time
import traceback

# make `chat.protocol` importable when running `python3 chat/server.py`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chat.protocol import send_msg, recv_msg  # noqa: E402
from common.constants import MAX_BULK, MAX_BAD_LOGINS  # noqa: E402
from chat.spam import SpamDetector  # noqa: E402
from chat.notifier import notify  # noqa: E402

CONTROL, ROOM, PM, BULK = 5000, 5001, 5002, 5003
KIND = {ROOM: "room", PM: "pm", BULK: "bulk"}
NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")

# ---- shared state: ALWAYS touch it inside `with lock:` ----
lock = threading.RLock()
spam = SpamDetector()
bad_logins = {}                   # ip -> consecutive bad logins
users = {}                        # name -> sha256(password)
tokens = {}                       # token -> name
online = {}                       # name -> Conn on port 5000
rooms = {}                        # room -> set of names
attached = {"room": {}, "pm": {}, "bulk": {}} # kind -> {name: Conn}

class Conn:
    """One client connection. send_lock stops two threads mixing frames."""
    def __init__(self, sock, addr):
        self.sock = sock
        self.ip = addr[0]
        self.user = None
        self.send_lock = threading.Lock()

    def send(self, obj):
        with self.send_lock:
            send_msg(self.sock, obj)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def emit(event, user, ip, **extra):
    """Tell the SDN controller (blocking, 1 s timeout, never raises)."""
    ok_sent = notify(event, user, ip,
                     room=extra.get("room"), reason=extra.get("reason"))
    print(f"[event] {event} user={user} ip={ip} {extra} sent={ok_sent}")


def ok(conn, req, **extra):
    conn.send({"id": req.get("id"), "type": "OK", **extra})


def err(conn, req, code, msg=""):
    conn.send({"id": req.get("id"), "type": "ERR", "code": code, "msg": msg})


def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()


def push(kind, name, obj):
    """Send an unsolicited message to a user's attached connection, if any."""
    with lock:
        c = attached[kind].get(name)
    if c:
        try:
            c.send(obj)
        except OSError:
            pass


# ---------------- port 5000 ----------------
def handle_control(conn, req):
    t = req.get("type")

    if t in ("REGISTER", "LOGIN"):
        user, pw = req.get("user"), req.get("password")
        if not (isinstance(user, str) and NAME_RE.match(user)
                and isinstance(pw, str) and pw):
            return err(conn, req, "BAD_REQUEST", "need valid user and password")
        if conn.user:
            return err(conn, req, "BAD_REQUEST", "already logged in")
        code = None
        with lock:
            if t == "REGISTER" and user in users:
                code = "USER_EXISTS"
            elif t == "LOGIN" and users.get(user) != hash_pw(pw):
                code = "BAD_CREDENTIALS"
            else:
                if t == "REGISTER":
                    users[user] = hash_pw(pw)
                token = secrets.token_hex(8)
                tokens[token] = user
                online[user] = conn
                conn.user = user
        if code:
            return err(conn, req, code)
        emit(t.lower(), user, conn.ip)
        return ok(conn, req, token=token)

    if not conn.user:
        return err(conn, req, "NOT_LOGGED_IN", "register or login first")

    if t == "JOIN":
        room = req.get("room")
        if not (isinstance(room, str) and NAME_RE.match(room)):
            return err(conn, req, "BAD_REQUEST", "bad room name")
        with lock:
            rooms.setdefault(room, set()).add(conn.user)
            members = sorted(rooms[room])
        emit("join", conn.user, conn.ip, room=room)
        return ok(conn, req, members=members)

    if t == "LEAVE":
        room = req.get("room")
        code = None
        with lock:
            members = rooms.get(room) if isinstance(room, str) else None
            if members is None:
                code = "NO_SUCH_ROOM"
            elif conn.user not in members:
                code = "NOT_IN_ROOM"
            else:
                members.discard(conn.user)
                if not members:
                    del rooms[room]
        if code:
            return err(conn, req, code)
        emit("leave", conn.user, conn.ip, room=room)
        return ok(conn, req)

    if t == "LIST":
        what = req.get("what")
        with lock:
            items = (sorted(rooms) if what == "rooms"
                     else sorted(online) if what == "users" else None)
        if items is None:
            return err(conn, req, "BAD_REQUEST", "what must be rooms or users")
        return ok(conn, req, items=items)

    return err(conn, req, "BAD_REQUEST", f"unknown type {t}")


# ---------------- ports 5001 / 5002 ----------------
def handle_attach(conn, req, port):
    if req.get("type") != "ATTACH":
        return err(conn, req, "BAD_TOKEN", "send ATTACH first")
    with lock:
        name = tokens.get(req.get("token"))
        if name:
            conn.user = name
            attached[KIND[port]][name] = conn
    if not name:
        return err(conn, req, "BAD_TOKEN")
    return ok(conn, req)

def handle_bulk(conn, req):
    if req.get("type") != "BULK":
        return err(conn, req, "BAD_REQUEST", "unknown type")
    size = req.get("size")
    if not (isinstance(size, int) and not isinstance(size, bool)
            and 0 < size <= MAX_BULK):
        return err(conn, req, "BAD_REQUEST", f"size must be 1..{MAX_BULK}")
    ok(conn, req)
    conn.send({"type": "BULK_DATA", "data": "x" * size})

def handle_data(conn, req, port):
    if port == BULK:
        return handle_bulk(conn, req)
    t = req.get("type")
    text = req.get("text")
    if not isinstance(text, str):
        return err(conn, req, "BAD_REQUEST", "text must be a string")
    if spam.record(conn.user, time.time()):
        emit("flag", conn.user, conn.ip, reason="spam")

    if port == ROOM and t == "MSG_ROOM":
        room = req.get("room")
        code, targets = None, []
        with lock:
            members = rooms.get(room) if isinstance(room, str) else None
            if members is None:
                code = "NO_SUCH_ROOM"
            elif conn.user not in members:
                code = "NOT_IN_ROOM"
            else:
                targets = [n for n in members if n != conn.user]
        if code:
            return err(conn, req, code)
        msg = {"type": "MSG", "kind": "room", "room": room,
               "from": conn.user, "text": text, "ts": time.time()}
        if "seq" in req:
            msg["seq"] = req["seq"]
        for n in targets:
            push("room", n, msg)
        return ok(conn, req)

    if port == PM and t == "MSG_PM":
        to = req.get("to")
        with lock:
            exists = isinstance(to, str) and to in users
            has_conn = isinstance(to, str) and to in attached["pm"]
        if not exists:
            return err(conn, req, "NO_SUCH_USER")
        if not has_conn:
            return err(conn, req, "OFFLINE")
        msg = {"type": "MSG", "kind": "pm", "from": conn.user,
               "text": text, "ts": time.time()}
        if "seq" in req:
            msg["seq"] = req["seq"]
        push("pm", to, msg)
        return ok(conn, req)

    return err(conn, req, "BAD_REQUEST", f"unknown type {t}")


# ---------------- per-connection loop ----------------
def cleanup(conn, port):
    conn.close()
    user = conn.user
    if user is None:
        return
    if port == CONTROL:
        with lock:
            if online.get(user) is conn:
                del online[user]
            for r in list(rooms):
                rooms[r].discard(user)
                if not rooms[r]:
                    del rooms[r]
            for tok in [k for k, v in tokens.items() if v == user]:
                del tokens[tok]
            extras = [attached[k].pop(user, None) for k in attached]
        for c in extras:
            if c:
                c.close()
        if spam.forget(user):
          emit("unflag", user, conn.ip, reason="spam")
        emit("disconnect", user, conn.ip)
    else:
        with lock:
            if attached[KIND[port]].get(user) is conn:
                del attached[KIND[port]][user]


def serve_client(sock, addr, port):
    conn = Conn(sock, addr)
    try:
        while True:
            try:
                req = recv_msg(sock)
            except ValueError as e:        # bad frame: stream is unreliable, close
                err(conn, {}, "BAD_REQUEST", str(e))
                break
            if port == CONTROL:
                handle_control(conn, req)
            elif conn.user is None:
                handle_attach(conn, req, port)
            else:
                handle_data(conn, req, port)
    except OSError:                         # includes ConnectionError (peer left)
        pass
    except Exception:
        traceback.print_exc()
    finally:
        cleanup(conn, port)

def spam_watch():
    """Every second, unflag users who have been quiet long enough."""
    while True:
        time.sleep(1)
        now = time.time()
        for user in spam.flagged_users():
            if spam.quiet(user, now):
                with lock:
                    c = online.get(user)
                emit("unflag", user, c.ip if c else "", reason="spam")

def listen(host, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    s.listen()
    print(f"listening on {host}:{port}")
    while True:
        sock, addr = s.accept()
        threading.Thread(target=serve_client, args=(sock, addr, port),
                         daemon=True).start()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()
    threading.Thread(target=spam_watch, daemon=True).start()
    for p in (CONTROL, ROOM, PM, BULK):
        threading.Thread(target=listen, args=(args.host, p), daemon=True).start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("bye")
