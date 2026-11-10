"""Chat server (Part A). Implements docs/interfaces.md sections 2-4.
Ports: 5000 control, 5001 room chat, 5002 private messages, 5003 bulk and file transfer.
"""
import argparse
import base64
import binascii
import hashlib
import hmac
import json
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
from common.constants import MAX_BULK, MAX_BAD_LOGINS, MAX_FILE, FILE_CHUNK  # noqa: E402
from chat.spam import SpamDetector  # noqa: E402
from chat.notifier import notify  # noqa: E402

CONTROL, ROOM, PM, BULK = 5000, 5001, 5002, 5003
KIND = {ROOM: "room", PM: "pm", BULK: "bulk"}
NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")

# ---- shared state: ALWAYS touch it inside `with lock:` ----
lock = threading.RLock()
spam = SpamDetector()
bad_logins = {}                   # ip -> consecutive bad logins
users = {}                        # name -> "pbkdf2$<salt hex>$<hash hex>" (see hash_pw)
files = {}                        # fid -> shared file (in memory, oldest dropped first)
FILE_LIMIT, STORE_LIMIT = 50, 64 * 1024 * 1024
DB_PATH = None                    # set by --db: accounts are saved there and survive a restart
tokens = {}                       # token -> name
online = {}                       # name -> Conn on port 5000
rooms = {}                        # room -> set of names
attached = {"room": {}, "pm": {}, "bulk": {}} # kind -> {name: Conn}
history = {}                      # room -> its last HISTORY_LIMIT messages (kept after the room empties, in memory only)
HISTORY_LIMIT, HISTORY_ROOMS, HISTORY_BYTES = 20, 200, 40000

class Conn:
    """One client connection. send_lock stops two threads mixing frames."""
    def __init__(self, sock, addr):
        self.sock = sock
        self.ip = addr[0]
        self.user = None
        self.send_lock = threading.Lock()
        self.upload = None

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


PBKDF2_ROUNDS = 20_000


def hash_pw(pw, salt=None):
    """Salted PBKDF2-SHA256. The salt is stored inside the result so a login can recompute it."""
    salt = salt if salt is not None else secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, PBKDF2_ROUNDS)
    return "pbkdf2$%s$%s" % (salt.hex(), h.hex())


def check_pw(pw, stored):
    try:
        _, salt, _ = stored.split("$")
        return hmac.compare_digest(hash_pw(pw, bytes.fromhex(salt)), stored)
    except (ValueError, AttributeError):
        return False


def save_users():
    """Write accounts to --db (atomic replace). Called with `lock` held. Never raises."""
    if not DB_PATH:
        return
    try:
        tmp = DB_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(users, f)
        os.replace(tmp, DB_PATH)
    except OSError as e:
        print(f"[db] could not save accounts: {e}")


def load_users():
    try:
        with open(DB_PATH) as f:
            data = json.load(f)
        if isinstance(data, dict):
            users.update({k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)})
        print(f"[db] loaded {len(users)} accounts from {DB_PATH}")
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        print(f"[db] could not read {DB_PATH}: {e}")


def push(kind, name, obj):
    """Send an unsolicited message to a user's attached connection, if any."""
    with lock:
        c = attached[kind].get(name)
    if c:
        try:
            c.send(obj)
        except OSError:
            pass


def remember(room, msg):
    """Keep the last HISTORY_LIMIT messages of a room, and at most HISTORY_ROOMS rooms."""
    with lock:
        h = history.setdefault(room, [])
        h.append(msg)
        del h[:-HISTORY_LIMIT]
        while len(history) > HISTORY_ROOMS:
            history.pop(next(iter(history)))


def recent(room):
    """The newest messages of a room that fit comfortably in one frame, oldest first."""
    with lock:
        h = list(history.get(room, ()))
    out, size = [], 0
    for m in reversed(h):
        size += len(json.dumps(m))
        if size > HISTORY_BYTES:
            break
        out.append(m)
    return out[::-1]


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
            stored = users.get(user)
        hashed = hash_pw(pw) if t == "REGISTER" else None        # slow hashing happens outside the lock
        login_ok = t == "LOGIN" and stored is not None and check_pw(pw, stored)
        with lock:
            if t == "REGISTER" and user in users:
                code = "USER_EXISTS"
            elif t == "LOGIN" and not login_ok:
                code = "BAD_CREDENTIALS"
            else:
                if t == "REGISTER":
                    users[user] = hashed
                    save_users()
                token = secrets.token_hex(8)
                tokens[token] = user
                if online.get(user) not in (None, conn):
                    tokens.pop(token, None)      # this sign-in is refused: drop its unused token
                    code = "ALREADY_ONLINE"      # the first session stays untouched
                else:
                    online[user] = conn
                    conn.user = user
        if code == "BAD_CREDENTIALS":
            with lock:
                bad_logins[conn.ip] = bad_logins.get(conn.ip, 0) + 1
                tripped = bad_logins[conn.ip] >= MAX_BAD_LOGINS
                if tripped:
                    bad_logins[conn.ip] = 0
            if tripped:
                emit("block", user, conn.ip, reason="bad_login")
        if code:
            return err(conn, req, code)
        with lock:
            bad_logins.pop(conn.ip, None)
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
        return ok(conn, req, members=members, history=recent(room))

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

def safe_name(name):
    """A file name with no path and only plain characters, at most 80 long."""
    name = os.path.basename(str(name).replace("\\", "/"))
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")[:80]
    return name or "file"


def store_file(rec):
    """Keep a shared file in memory; drop the oldest ones past the count or byte limit."""
    with lock:
        files[rec["fid"]] = rec
        while len(files) > FILE_LIMIT or sum(f["size"] for f in files.values()) > STORE_LIMIT:
            files.pop(next(iter(files)))


def can_read(user, rec):
    with lock:
        if rec["room"]:
            return user in rooms.get(rec["room"], ())
    return user in (rec["owner"], rec["to"])


def handle_file_up(conn, req):
    name, size = safe_name(req.get("name", "file")), req.get("size")
    room, to = req.get("room"), req.get("to")
    if not (isinstance(size, int) and not isinstance(size, bool) and 0 < size <= MAX_FILE):
        return err(conn, req, "BAD_REQUEST", f"size must be 1..{MAX_FILE}")
    if (room is None) == (to is None):
        return err(conn, req, "BAD_REQUEST", "give exactly one of room or to")
    with lock:
        if room is not None:
            members = rooms.get(room) if isinstance(room, str) else None
            if members is None:
                return err(conn, req, "NO_SUCH_ROOM")
            if conn.user not in members:
                return err(conn, req, "NOT_IN_ROOM")
        else:
            if not (isinstance(to, str) and to in users):
                return err(conn, req, "NO_SUCH_USER")
            if to not in attached["pm"]:
                return err(conn, req, "OFFLINE")
    fid = secrets.token_hex(6)
    conn.upload = {"fid": fid, "name": name, "size": size, "room": room, "to": to, "buf": bytearray(), "next": 0}
    return ok(conn, req, fid=fid, chunk=FILE_CHUNK)


def handle_file_chunk(conn, req):
    up = getattr(conn, "upload", None)
    if not up or req.get("fid") != up["fid"]:
        return err(conn, req, "BAD_REQUEST", "no upload in progress")
    try:
        data = base64.b64decode(req.get("data", ""), validate=True)
    except (binascii.Error, ValueError, TypeError):
        conn.upload = None
        return err(conn, req, "BAD_REQUEST", "chunk is not base64")
    if req.get("seq") != up["next"] or len(data) > FILE_CHUNK or len(up["buf"]) + len(data) > up["size"]:
        conn.upload = None
        return err(conn, req, "BAD_REQUEST", "chunk out of order or too large")
    up["buf"] += data
    up["next"] += 1
    if not req.get("last"):
        return
    conn.upload = None
    if len(up["buf"]) != up["size"]:
        return err(conn, req, "BAD_REQUEST", "file is shorter than announced")
    rec = {"fid": up["fid"], "name": up["name"], "size": up["size"], "owner": conn.user, "room": up["room"],
           "to": up["to"], "data": bytes(up["buf"]), "ts": time.time()}
    store_file(rec)
    if spam.record(conn.user, rec["ts"]):          # a file counts as a message for the spam rule
        emit("flag", conn.user, conn.ip, reason="spam")
    note = {"type": "MSG", "kind": "room" if rec["room"] else "pm", "from": conn.user,
            "text": "[file] %s (%d bytes)" % (rec["name"], rec["size"]), "ts": rec["ts"],
            "file": {"fid": rec["fid"], "name": rec["name"], "size": rec["size"]}}
    if rec["room"]:
        note["room"] = rec["room"]
        remember(rec["room"], note)
        with lock:
            targets = [n for n in rooms.get(rec["room"], ()) if n != conn.user]
    else:
        targets = [rec["to"]]
    for n in targets:
        push("room" if rec["room"] else "pm", n, note)
    return ok(conn, req, fid=rec["fid"], name=rec["name"], size=rec["size"])


def handle_file_get(conn, req):
    with lock:
        rec = files.get(req.get("fid"))
    if rec is None:
        return err(conn, req, "NO_SUCH_FILE", "unknown or expired file")
    if not can_read(conn.user, rec):
        return err(conn, req, "NOT_ALLOWED", "you are not in this conversation")
    data = rec["data"]
    chunks = [data[i:i + FILE_CHUNK] for i in range(0, len(data), FILE_CHUNK)]
    ok(conn, req, fid=rec["fid"], name=rec["name"], size=rec["size"], chunks=len(chunks))
    for i, c in enumerate(chunks):
        conn.send({"type": "FILE_CHUNK", "fid": rec["fid"], "seq": i,
                   "data": base64.b64encode(c).decode(), "last": i == len(chunks) - 1})


def handle_bulk(conn, req):
    t = req.get("type")
    if t == "FILE_UP":
        return handle_file_up(conn, req)
    if t == "FILE_CHUNK":
        return handle_file_chunk(conn, req)
    if t == "FILE_GET":
        return handle_file_get(conn, req)
    if t != "BULK":
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
        remember(room, msg)
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
    ap.add_argument("--db", help="JSON file where accounts are kept between restarts (off by default)")
    args = ap.parse_args()
    if args.db:
        DB_PATH = args.db
        load_users()
    threading.Thread(target=spam_watch, daemon=True).start()
    for p in (CONTROL, ROOM, PM, BULK):
        threading.Thread(target=listen, args=(args.host, p), daemon=True).start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("bye")
