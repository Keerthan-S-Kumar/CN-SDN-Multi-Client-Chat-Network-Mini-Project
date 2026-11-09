"""Automated tests for the chat server (Part A, D1 "TCP sockets").

Black-box: starts the REAL chat/server.py as a subprocess on 127.0.0.1 and talks
to it over TCP using chat/protocol.py, exactly like chat/client.py does.
A tiny fake controller (HTTP) receives the server's POST /event calls, so no
Ryu or Mininet is needed.

Run from the repo root:   pip install pytest && python3 -m pytest tests/ -v
Requires ports 5000-5003 to be free (stop any running server first).
"""
import itertools
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from chat.protocol import send_msg, recv_msg  # noqa: E402
from common import constants as C  # noqa: E402

HOST = "127.0.0.1"
CTL = C.PORT_CONTROL
ROOM = C.PORT_ROOM
PM = C.PORT_PM
BULK = getattr(C, "PORT_BULK", 5003)
SPAM_MSGS = getattr(C, "SPAM_MSGS", 30)
MAX_BAD_LOGINS = getattr(C, "MAX_BAD_LOGINS", 5)

_ids = itertools.count(1)


# --------------------------------------------------------------------------
# fake controller: records every POST /event the server sends
# --------------------------------------------------------------------------
class FakeController:
    def __init__(self):
        self.events = []
        self.lock = threading.Lock()
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self._reply(200, {"ok": True})

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                try:
                    ev = json.loads(self.rfile.read(n))
                except ValueError:
                    return self._reply(400, {"ok": False, "error": "bad json"})
                with outer.lock:
                    outer.events.append(ev)
                self._reply(200, {"ok": True})

        self.httpd = ThreadingHTTPServer((HOST, 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def wait_event(self, timeout=3.0, **match):
        """Return the first recorded event whose fields match, or None."""
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                for ev in self.events:
                    if all(ev.get(k) == v for k, v in match.items()):
                        return ev
            time.sleep(0.05)
        return None


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
def _port_in_use(port):
    try:
        socket.create_connection((HOST, port), timeout=0.5).close()
        return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def controller():
    c = FakeController()
    yield c
    c.httpd.shutdown()


@pytest.fixture(scope="session", autouse=True)
def server(controller):
    busy = [p for p in (CTL, ROOM, PM, BULK) if _port_in_use(p)]
    if busy:
        pytest.fail(f"ports {busy} already in use: stop any running chat server first")
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT
    env["CONTROLLER_URL"] = f"http://{HOST}:{controller.port}"
    log = tempfile.NamedTemporaryFile("w+", suffix=".log", delete=False)
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "chat", "server.py"), "--host", HOST],
        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    deadline = time.time() + 8
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        if _port_in_use(CTL):
            break
        time.sleep(0.1)
    if proc.poll() is not None or not _port_in_use(CTL):
        proc.kill()
        log.seek(0)
        pytest.fail("server did not start:\n" + log.read())
    yield proc
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def connect(port, timeout=3.0):
    s = socket.create_connection((HOST, port), timeout=timeout)
    s.settimeout(timeout)
    return s


def call(sock, type_, **fields):
    """Send a request and read its reply (only for sockets that get no pushes)."""
    rid = next(_ids)
    send_msg(sock, {"id": rid, "type": type_, **fields})
    r = recv_msg(sock)
    assert r.get("id") == rid, f"reply id mismatch: {r}"
    return r


def recv_push(sock, timeout=3.0):
    """Next non-OK frame on an attached socket, or None on timeout."""
    sock.settimeout(timeout)
    end = time.time() + timeout
    while time.time() < end:
        try:
            m = recv_msg(sock)
        except socket.timeout:
            return None
        if m.get("type") != "OK":
            return m
    return None


def uname():
    return "t" + uuid.uuid4().hex[:10]


class User:
    """A logged-in client with control, room and PM connections."""

    def __init__(self, name=None, attach=True):
        self.name = name or uname()
        self.ctl = connect(CTL)
        r = call(self.ctl, "REGISTER", user=self.name, password="pw")
        assert r["type"] == "OK", r
        self.token = r["token"]
        self.room = self.pm = None
        if attach:
            self.room = connect(ROOM)
            assert call(self.room, "ATTACH", token=self.token)["type"] == "OK"
            self.pm = connect(PM)
            assert call(self.pm, "ATTACH", token=self.token)["type"] == "OK"

    def join(self, room):
        r = call(self.ctl, "JOIN", room=room)
        assert r["type"] == "OK", r
        return r

    def say(self, room, text, **extra):
        send_msg(self.room, {"id": next(_ids), "type": "MSG_ROOM",
                             "room": room, "text": text, **extra})

    def pm_to(self, to, text):
        send_msg(self.pm, {"id": next(_ids), "type": "MSG_PM", "to": to, "text": text})

    def close(self):
        for s in (self.ctl, self.room, self.pm):
            if s:
                try:
                    s.close()
                except OSError:
                    pass


@pytest.fixture
def make_user():
    made = []

    def _make(**kw):
        u = User(**kw)
        made.append(u)
        return u

    yield _make
    for u in made:
        u.close()


def poll(fn, timeout=3.0, interval=0.1):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(interval)
    return None


# --------------------------------------------------------------------------
# registration and login
# --------------------------------------------------------------------------
def test_register_returns_token_and_notifies_controller(controller, make_user):
    u = make_user()
    assert isinstance(u.token, str) and u.token
    ev = controller.wait_event(event="register", user=u.name)
    assert ev is not None, "server did not POST a register event"
    assert ev["ip"] == "127.0.0.1"


def test_duplicate_register_rejected(make_user):
    u = make_user()
    s = connect(CTL)
    r = call(s, "REGISTER", user=u.name, password="other")
    s.close()
    assert r["type"] == "ERR" and r["code"] == "USER_EXISTS"


@pytest.mark.parametrize("bad", ["", "has space", "bad-name", "x" * 17, "né"])
def test_invalid_username_rejected(bad):
    s = connect(CTL)
    r = call(s, "REGISTER", user=bad, password="pw")
    s.close()
    assert r["type"] == "ERR" and r["code"] == "BAD_REQUEST"


def test_register_missing_password_is_bad_request():
    s = connect(CTL)
    r = call(s, "REGISTER", user=uname())
    s.close()
    assert r["type"] == "ERR" and r["code"] == "BAD_REQUEST"


def test_login_success_and_login_event(controller, make_user):
    u = make_user(attach=False)
    u.ctl.close()  # one session per account: end the first before signing in again
    time.sleep(0.3)
    s = connect(CTL)
    r = call(s, "LOGIN", user=u.name, password="pw")
    s.close()
    assert r["type"] == "OK" and r["token"]
    assert controller.wait_event(event="login", user=u.name)


def test_login_wrong_password(make_user):
    u = make_user(attach=False)
    s = connect(CTL)
    r = call(s, "LOGIN", user=u.name, password="WRONG")
    s.close()
    assert r["type"] == "ERR" and r["code"] == "BAD_CREDENTIALS"


def test_login_unknown_user():
    s = connect(CTL)
    r = call(s, "LOGIN", user=uname(), password="pw")
    s.close()
    assert r["type"] == "ERR" and r["code"] in ("BAD_CREDENTIALS", "NO_SUCH_USER")


def test_second_login_is_refused_and_first_session_survives(make_user):
    room = "r" + uuid.uuid4().hex[:8]
    a, b = make_user(), make_user()
    a.join(room)
    b.join(room)
    s = connect(CTL)
    r = call(s, "LOGIN", user=a.name, password="pw")
    s.close()  # the refused attempt goes away, like closing an incognito tab
    assert r["type"] == "ERR" and r["code"] == "ALREADY_ONLINE"
    time.sleep(0.3)
    assert call(a.ctl, "LIST", what="rooms")["type"] == "OK", "first session was logged out"
    b.say(room, "still there?")
    m = recv_push(a.room)
    assert m and m["type"] == "MSG" and m["text"] == "still there?", "first session lost its room"


def test_commands_require_login():
    s = connect(CTL)
    for type_, fields in (("JOIN", {"room": "x"}), ("LEAVE", {"room": "x"}),
                          ("LIST", {"what": "rooms"})):
        r = call(s, type_, **fields)
        assert r["type"] == "ERR" and r["code"] == "NOT_LOGGED_IN", (type_, r)
    s.close()


def test_attach_with_bad_token_rejected():
    for port in (ROOM, PM):
        s = connect(port)
        r = call(s, "ATTACH", token="not-a-real-token")
        s.close()
        assert r["type"] == "ERR" and r["code"] == "BAD_TOKEN"


def test_chat_port_requires_attach_first():
    s = connect(ROOM)
    r = call(s, "MSG_ROOM", room="x", text="hi")
    s.close()
    assert r["type"] == "ERR"


# --------------------------------------------------------------------------
# rooms
# --------------------------------------------------------------------------
def test_join_creates_room_and_lists_members(controller, make_user):
    room = "r" + uuid.uuid4().hex[:8]
    a, b = make_user(), make_user()
    assert a.join(room)["members"] == [a.name]
    assert set(b.join(room)["members"]) == {a.name, b.name}
    ev = controller.wait_event(event="join", user=b.name, room=room)
    assert ev is not None


def test_leave_room_and_not_in_room_errors(controller, make_user):
    room = "r" + uuid.uuid4().hex[:8]
    a, b = make_user(), make_user()
    a.join(room)
    b.join(room)  # keeps the room alive after a leaves (empty rooms are deleted)
    assert call(a.ctl, "LEAVE", room=room)["type"] == "OK"
    assert controller.wait_event(event="leave", user=a.name, room=room)
    r = call(a.ctl, "LEAVE", room=room)
    assert r["type"] == "ERR" and r["code"] == "NOT_IN_ROOM"


def test_empty_room_is_deleted(make_user):
    room = "r" + uuid.uuid4().hex[:8]
    a = make_user()
    a.join(room)
    assert room in call(a.ctl, "LIST", what="rooms")["items"]
    assert call(a.ctl, "LEAVE", room=room)["type"] == "OK"
    assert room not in call(a.ctl, "LIST", what="rooms")["items"]


def test_leave_unknown_room(make_user):
    a = make_user()
    r = call(a.ctl, "LEAVE", room="nope" + uuid.uuid4().hex[:6])
    assert r["type"] == "ERR" and r["code"] in ("NO_SUCH_ROOM", "NOT_IN_ROOM")


def test_list_rooms_and_users(make_user):
    room = "r" + uuid.uuid4().hex[:8]
    a, b = make_user(), make_user()
    a.join(room)
    rooms = call(b.ctl, "LIST", what="rooms")
    users = call(b.ctl, "LIST", what="users")
    assert rooms["type"] == "OK" and room in rooms["items"]
    assert users["type"] == "OK" and {a.name, b.name} <= set(users["items"])


def test_list_bad_argument(make_user):
    a = make_user()
    r = call(a.ctl, "LIST", what="widgets")
    assert r["type"] == "ERR" and r["code"] == "BAD_REQUEST"


# --------------------------------------------------------------------------
# room messages
# --------------------------------------------------------------------------
def test_room_message_reaches_members_only(make_user):
    room = "r" + uuid.uuid4().hex[:8]
    a, b, c, outsider = make_user(), make_user(), make_user(), make_user()
    for u in (a, b, c):
        u.join(room)
    a.say(room, "hello room")
    for u in (b, c):
        m = recv_push(u.room)
        assert m and m["type"] == "MSG" and m["kind"] == "room"
        assert m["room"] == room and m["from"] == a.name and m["text"] == "hello room"
        assert isinstance(m["ts"], (int, float))
    assert recv_push(outsider.room, timeout=0.7) is None, "non-member got a room message"


def test_room_message_copies_seq(make_user):
    room = "r" + uuid.uuid4().hex[:8]
    a, b = make_user(), make_user()
    a.join(room)
    b.join(room)
    a.say(room, "numbered", seq=42)
    m = recv_push(b.room)
    assert m and m.get("seq") == 42


def test_room_message_to_unknown_room(make_user):
    a = make_user()
    a.say("ghost" + uuid.uuid4().hex[:6], "anyone?")
    m = recv_push(a.room)
    assert m and m["type"] == "ERR" and m["code"] == "NO_SUCH_ROOM"


def test_room_message_when_not_a_member(make_user):
    room = "r" + uuid.uuid4().hex[:8]
    a, b = make_user(), make_user()
    a.join(room)
    b.say(room, "let me in")
    m = recv_push(b.room)
    assert m and m["type"] == "ERR" and m["code"] == "NOT_IN_ROOM"
    assert recv_push(a.room, timeout=0.5) is None


def test_no_messages_after_leaving(make_user):
    room = "r" + uuid.uuid4().hex[:8]
    a, b = make_user(), make_user()
    a.join(room)
    b.join(room)
    call(b.ctl, "LEAVE", room=room)
    a.say(room, "bye b")
    assert recv_push(b.room, timeout=0.7) is None


# --------------------------------------------------------------------------
# private messages
# --------------------------------------------------------------------------
def test_private_message_only_reaches_recipient(make_user):
    a, b, c = make_user(), make_user(), make_user()
    a.pm_to(b.name, "psst")
    m = recv_push(b.pm)
    assert m and m["type"] == "MSG" and m["kind"] == "pm"
    assert m["from"] == a.name and m["text"] == "psst"
    assert recv_push(c.pm, timeout=0.7) is None
    assert recv_push(c.room, timeout=0.2) is None


def test_private_message_unknown_user(make_user):
    a = make_user()
    a.pm_to("nobody" + uuid.uuid4().hex[:6], "hello?")
    m = recv_push(a.pm)
    assert m and m["type"] == "ERR" and m["code"] == "NO_SUCH_USER"


def test_private_message_to_offline_user(make_user):
    a, b = make_user(), make_user()
    b.close()

    def offline():
        a.pm_to(b.name, "are you there")
        m = recv_push(a.pm, timeout=0.5)
        return m if m and m["type"] == "ERR" and m["code"] == "OFFLINE" else None

    assert poll(offline, timeout=4), "expected OFFLINE error for a disconnected user"


# --------------------------------------------------------------------------
# robustness: bad input must never crash the server
# --------------------------------------------------------------------------
def _server_still_works():
    u = User()
    u.close()


def test_unknown_message_type(make_user):
    a = make_user()
    r = call(a.ctl, "DANCE")
    assert r["type"] == "ERR" and r["code"] == "BAD_REQUEST"
    _server_still_works()


def test_malformed_json_frame_does_not_crash_server():
    s = connect(CTL)
    payload = b"this is not json {{{"
    s.sendall(struct.pack(">I", len(payload)) + payload)
    try:
        r = recv_msg(s)  # either an ERR reply...
        assert r["type"] == "ERR"
    except (ConnectionError, OSError, ValueError):
        pass  # ...or the server drops the connection; both are acceptable
    s.close()
    _server_still_works()


def test_json_that_is_not_an_object_does_not_crash_server():
    s = connect(CTL)
    payload = b"[1, 2, 3]"
    s.sendall(struct.pack(">I", len(payload)) + payload)
    try:
        r = recv_msg(s)
        assert r["type"] == "ERR"
    except (ConnectionError, OSError, ValueError):
        pass
    s.close()
    _server_still_works()


def test_oversized_frame_header_does_not_crash_server():
    s = connect(CTL)
    s.sendall(struct.pack(">I", 0xFFFFFFFF))
    try:
        s.recv(1)  # server should close (or answer); must not hang forever
    except (ConnectionError, OSError):
        pass
    s.close()
    _server_still_works()


def test_truncated_frame_then_disconnect_does_not_crash_server():
    s = connect(CTL)
    s.sendall(struct.pack(">I", 100) + b"abc")  # promises 100 bytes, sends 3
    s.close()
    _server_still_works()


def test_abrupt_disconnect_cleans_up_user_and_room(controller, make_user):
    room = "r" + uuid.uuid4().hex[:8]
    a, b, c = make_user(), make_user(), make_user()
    a.join(room)
    b.join(room)
    a.close()  # no LEAVE, no goodbye

    gone = poll(lambda: a.name not in call(b.ctl, "LIST", what="users")["items"], timeout=4)
    assert gone, "disconnected user still listed as online"
    assert controller.wait_event(event="disconnect", user=a.name)
    assert a.name not in c.join(room)["members"], "dead user left in room membership"


def test_user_can_log_back_in_after_disconnect(make_user):
    a = make_user()
    a.close()
    time.sleep(0.3)
    s = connect(CTL)
    r = call(s, "LOGIN", user=a.name, password="pw")
    s.close()
    assert r["type"] == "OK"


# --------------------------------------------------------------------------
# concurrency: many simultaneous connections
# --------------------------------------------------------------------------
def test_many_concurrent_clients_all_messages_delivered():
    n = 20
    room = "load" + uuid.uuid4().hex[:6]
    barrier = threading.Barrier(n, timeout=20)
    errors, received = [], {}
    users = []

    def worker(i):
        try:
            u = User()
            users.append(u)
            u.join(room)
            barrier.wait()  # everyone has joined before anyone speaks
            u.say(room, f"hi from {i}", seq=i)
            got = set()
            while len(got) < n - 1:
                m = recv_push(u.room, timeout=10)
                if m is None:
                    break
                if m["type"] == "MSG":
                    got.add(m["seq"])
            received[i] = got
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=40)
    for u in users:
        u.close()

    assert not errors, errors
    assert len(received) == n
    for i, got in received.items():
        assert got == set(range(n)) - {i}, f"client {i} missed {set(range(n)) - {i} - got}"


# --------------------------------------------------------------------------
# bulk class (port 5003)
# --------------------------------------------------------------------------
def test_bulk_transfer(make_user):
    u = make_user()
    s = connect(BULK)
    assert call(s, "ATTACH", token=u.token)["type"] == "OK"
    assert call(s, "BULK", size=1000)["type"] == "OK"
    m = recv_msg(s)
    s.close()
    assert m["type"] == "BULK_DATA" and len(m["data"]) == 1000


@pytest.mark.parametrize("size", [0, -5, "big", 10 ** 9])
def test_bulk_rejects_bad_size(make_user, size):
    u = make_user()
    s = connect(BULK)
    assert call(s, "ATTACH", token=u.token)["type"] == "OK"
    r = call(s, "BULK", size=size)
    s.close()
    assert r["type"] == "ERR" and r["code"] == "BAD_REQUEST"


# --------------------------------------------------------------------------
# server -> controller notifications for abuse (kept last on purpose)
# --------------------------------------------------------------------------
def test_spam_user_is_flagged_to_controller(controller, make_user):
    room = "spam" + uuid.uuid4().hex[:6]
    a = make_user()
    a.join(room)
    for i in range(SPAM_MSGS + 10):
        a.say(room, f"spam {i}")
    ev = controller.wait_event(timeout=5, event="flag", user=a.name)
    assert ev is not None, "no flag event after message flood"
    assert ev.get("reason") == "spam"
    _server_still_works()  # server must not fall over while flagging


def test_repeated_bad_logins_trigger_block_event(controller, make_user):
    u = make_user(attach=False)
    u.ctl.close()  # one session per account: end the first before signing in again
    time.sleep(0.3)
    ok = connect(CTL)
    assert call(ok, "LOGIN", user=u.name, password="pw")["type"] == "OK"  # resets the IP's counter
    ok.close()
    for _ in range(MAX_BAD_LOGINS):  # a fresh connection per attempt, like a real attacker
        s = connect(CTL)
        r = call(s, "LOGIN", user=u.name, password="WRONG")
        s.close()
        assert r["type"] == "ERR" and r["code"] == "BAD_CREDENTIALS"
    ev = controller.wait_event(timeout=5, event="block")
    assert ev is not None, "no block event after repeated bad logins"
    assert ev.get("reason") == "bad_login"

# --------------------------------------------------------------------------
# file sharing (port 5003)
# --------------------------------------------------------------------------
import base64  # noqa: E402


def bulk_socket(user):
    s = connect(BULK, timeout=5)
    assert call(s, "ATTACH", token=user.token)["type"] == "OK"
    return s


def upload(s, data, **target):
    r = call(s, "FILE_UP", name="../../etc/hello world.txt", size=len(data), **target)
    if r["type"] != "OK":
        return r
    chunk = r["chunk"]
    parts = [data[i:i + chunk] for i in range(0, len(data), chunk)] or [b""]
    for i, part in enumerate(parts):
        send_msg(s, {"id": next(_ids), "type": "FILE_CHUNK", "fid": r["fid"], "seq": i,
                     "data": base64.b64encode(part).decode(), "last": i == len(parts) - 1})
    return recv_msg(s)


def download(s, fid):
    r = call(s, "FILE_GET", fid=fid)
    if r["type"] != "OK":
        return r, None
    out = b""
    for _ in range(r["chunks"]):
        m = recv_msg(s)
        assert m["type"] == "FILE_CHUNK"
        out += base64.b64decode(m["data"])
    return r, out


def test_file_to_room_arrives_and_downloads(make_user):
    a, b = make_user(), make_user()
    room = "files" + uuid.uuid4().hex[:6]
    a.join(room), b.join(room)
    data = bytes(range(256)) * 400                      # 102 400 bytes: several chunks
    sa, sb = bulk_socket(a), bulk_socket(b)
    done = upload(sa, data, room=room)
    assert done["type"] == "OK" and done["size"] == len(data)
    assert done["name"] == "hello world.txt"            # path stripped
    note = recv_push(b.room)
    assert note["kind"] == "room" and note["room"] == room and note["from"] == a.name
    assert note["file"]["fid"] == done["fid"] and "[file]" in note["text"]
    meta, got = download(sb, done["fid"])
    assert got == data and meta["name"] == "hello world.txt"
    sa.close(), sb.close()


def test_file_to_private_message_only_recipient_can_read(make_user):
    a, b, c = make_user(), make_user(), make_user()
    sa, sb, sc = bulk_socket(a), bulk_socket(b), bulk_socket(c)
    done = upload(sa, b"secret notes", to=b.name)
    assert done["type"] == "OK"
    note = recv_push(b.pm)
    assert note["kind"] == "pm" and note["file"]["name"] == "hello world.txt"
    assert download(sb, done["fid"])[1] == b"secret notes"
    assert download(sa, done["fid"])[1] == b"secret notes"     # the sender may fetch it again
    r, _ = download(sc, done["fid"])
    assert r["type"] == "ERR" and r["code"] == "NOT_ALLOWED"
    for s in (sa, sb, sc):
        s.close()


def test_file_errors(make_user):
    a, b = make_user(), make_user(attach=False)
    s = bulk_socket(a)
    assert call(s, "FILE_UP", name="x", size=5, room="nowhere")["code"] == "NO_SUCH_ROOM"
    room = "r" + uuid.uuid4().hex[:6]
    b.join(room)
    assert call(s, "FILE_UP", name="x", size=5, room=room)["code"] == "NOT_IN_ROOM"
    assert call(s, "FILE_UP", name="x", size=5, to="ghost_" + uuid.uuid4().hex[:6])["code"] == "NO_SUCH_USER"
    assert call(s, "FILE_UP", name="x", size=5, to=b.name)["code"] == "OFFLINE"
    assert call(s, "FILE_UP", name="x", size=5)["code"] == "BAD_REQUEST"                       # no target
    assert call(s, "FILE_UP", name="x", size=5, room=room, to=b.name)["code"] == "BAD_REQUEST"  # two targets
    for size in (0, -1, C.MAX_FILE + 1, "big"):
        assert call(s, "FILE_UP", name="x", size=size, to=b.name)["code"] == "BAD_REQUEST"
    assert call(s, "FILE_GET", fid="nope")["code"] == "NO_SUCH_FILE"
    s.close()


def test_file_short_or_out_of_order_upload_is_rejected(make_user):
    a, b = make_user(), make_user()
    s = bulk_socket(a)
    r = call(s, "FILE_UP", name="x", size=10, to=b.name)
    send_msg(s, {"id": 1, "type": "FILE_CHUNK", "fid": r["fid"], "seq": 0,
                 "data": base64.b64encode(b"abc").decode(), "last": True})
    assert recv_msg(s)["code"] == "BAD_REQUEST"               # announced 10, sent 3
    r = call(s, "FILE_UP", name="x", size=10, to=b.name)
    send_msg(s, {"id": 2, "type": "FILE_CHUNK", "fid": r["fid"], "seq": 5,
                 "data": base64.b64encode(b"abc").decode(), "last": False})
    assert recv_msg(s)["code"] == "BAD_REQUEST"               # wrong sequence number
    r = call(s, "FILE_UP", name="x", size=10, to=b.name)
    send_msg(s, {"id": 3, "type": "FILE_CHUNK", "fid": r["fid"], "seq": 0, "data": "!!not base64!!", "last": False})
    assert recv_msg(s)["code"] == "BAD_REQUEST"
    s.close()


def test_passwords_are_salted_and_server_check_works():
    sys.path.insert(0, ROOT)
    from chat import server
    h1, h2 = server.hash_pw("pw"), server.hash_pw("pw")
    assert h1 != h2 and h1.startswith("pbkdf2$")                  # same password, different salt
    assert server.check_pw("pw", h1) and not server.check_pw("nope", h1)
    assert not server.check_pw("pw", "garbage")


def test_accounts_survive_a_restart_with_db(tmp_path):
    sys.path.insert(0, ROOT)
    from chat import server
    old_path, old_users = server.DB_PATH, dict(server.users)
    try:
        server.DB_PATH = str(tmp_path / "users.json")
        server.users.clear()
        server.users["zed"] = server.hash_pw("pw")
        with server.lock:
            server.save_users()
        server.users.clear()
        server.load_users()
        assert server.check_pw("pw", server.users["zed"])
        (tmp_path / "users.json").write_text("not json")          # a damaged file must not crash the server
        server.users.clear()
        server.load_users()
        assert server.users == {}
    finally:
        server.DB_PATH = old_path
        server.users.clear()
        server.users.update(old_users)
