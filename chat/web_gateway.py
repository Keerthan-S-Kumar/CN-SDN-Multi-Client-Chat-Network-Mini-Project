"""Browser front end for the chat. OWNER: B (extra, not part of the frozen contract).

Each browser session gets its own real TCP connections (5000 control, 5001 room, 5002 PM) to the chat server, using
the same protocol as chat/client.py. So the SDN gate still applies: the server sees this machine's IP, and the
controller allows, queues, throttles or blocks that IP exactly as it would for a terminal client.

  python3 chat/web_gateway.py --server 10.0.0.1 --port 8000        # then open http://localhost:8000
"""
import argparse
import json
import os
import queue
import secrets
import socket
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from chat.protocol import recv_msg, send_msg
from common.constants import CONTROLLER_URL, PORT_CONTROL, PORT_PM, PORT_ROOM

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "index.html")
CONTROLLER = CONTROLLER_URL     # where /snapshot and /metrics are read for the network card
IDLE_SECONDS = 45          # a session with no browser polling it for this long is closed


class GatewayError(Exception):
    def __init__(self, code, msg=""):
        super().__init__(code)
        self.code, self.msg = code, msg


class Session:
    """One logged-in browser: three sockets to the chat server and a queue of events for the page."""

    def __init__(self, server):
        self.server = server
        self.user = None
        self.token = None
        self.events = queue.Queue()
        self.touched = time.time()
        self.closed = False
        self.rooms = []
        self._id = 0
        self._lock = threading.Lock()
        self.ctl = self.room_sock = self.pm_sock = None

    def _rid(self):
        self._id += 1
        return self._id

    def _call(self, sock, type_, **fields):
        send_msg(sock, {"id": self._rid(), "type": type_, **fields})
        return recv_msg(sock)

    def _connect(self, port):
        s = socket.create_connection((self.server, port), timeout=5)
        s.settimeout(None)
        return s

    def login(self, mode, user, password):
        try:
            self.ctl = self._connect(PORT_CONTROL)
            r = self._call(self.ctl, "REGISTER" if mode == "register" else "LOGIN", user=user, password=password)
            if r["type"] == "ERR":
                raise GatewayError(r["code"], r.get("msg", ""))
            self.user, self.token = user, r["token"]
            socks = []
            for port in (PORT_ROOM, PORT_PM):
                s = self._connect(port)
                a = self._call(s, "ATTACH", token=self.token)
                if a["type"] == "ERR":
                    raise GatewayError(a["code"], a.get("msg", ""))
                socks.append(s)
            self.room_sock, self.pm_sock = socks
        except (OSError, ConnectionError, ValueError):
            self.close()
            raise GatewayError("SERVER_UNREACHABLE", "The chat server did not answer. Is it running, and does the network allow this host?")
        except GatewayError:
            self.close()
            raise
        for s in socks:
            threading.Thread(target=self._listen, args=(s,), daemon=True).start()

    def _listen(self, sock):
        try:
            while not self.closed:
                m = recv_msg(sock)
                if m.get("type") in ("MSG", "ERR"):
                    self.events.put(m)
        except (ConnectionError, OSError, ValueError):
            if not self.closed:
                self.events.put({"type": "CLOSED"})

    def control(self, type_, **fields):
        """Request/response on the control socket; one at a time."""
        with self._lock:
            try:
                r = self._call(self.ctl, type_, **fields)
            except (OSError, ConnectionError, ValueError):
                raise GatewayError("SERVER_UNREACHABLE", "Lost the connection to the chat server.")
        if r["type"] == "ERR":
            raise GatewayError(r["code"], r.get("msg", ""))
        return r

    def send(self, sock, type_, **fields):
        try:
            send_msg(sock, {"id": self._rid(), "type": type_, **fields})
        except (OSError, ValueError):
            raise GatewayError("SERVER_UNREACHABLE", "Lost the connection to the chat server.")

    def close(self):
        self.closed = True
        for s in (self.ctl, self.room_sock, self.pm_sock):
            try:
                if s:
                    s.close()
            except OSError:
                pass


SESSIONS = {}
SESSIONS_LOCK = threading.Lock()


def reaper():
    while True:
        time.sleep(5)
        now = time.time()
        with SESSIONS_LOCK:
            dead = [k for k, s in SESSIONS.items() if now - s.touched > IDLE_SECONDS or s.closed]
            for k in dead:
                SESSIONS.pop(k).close()


def fetch_json(path):
    with urllib.request.urlopen(CONTROLLER.rstrip("/") + path, timeout=1.0) as r:
        return json.load(r)


def network_view(ip):
    """What the SDN controller currently does to traffic from `ip`. Never raises: the card just says unavailable."""
    try:
        snap, met = fetch_json("/snapshot"), fetch_json("/metrics")
    except Exception:
        return {"available": False, "ip": ip}
    rules = snap.get("rules", {})
    extra = {}
    try:                                    # newer controllers also say how much this host sent and why it is limited
        mine = fetch_json("/traffic").get("hosts", {}).get(ip)
        if mine:
            extra.update(bytes=mine.get("total", 0), bps=mine.get("bps", 0), by_class=mine.get("bytes", {}))
        acts = [a for a in fetch_json("/explain").get("actions", []) if a.get("ip") == ip and a.get("category") != "vip"]
        if acts:
            extra["reasons"] = [{"category": a["category"], "reason": a["reason"], "expires_in_s": a.get("expires_in_s")}
                                for a in acts]
    except Exception:
        pass
    return {**extra, "available": True, "ip": ip, "mode": met.get("mode"),
            "allowed": ip in rules.get("allow", []), "vip": ip in rules.get("vip", []),
            "throttled": ip in rules.get("throttle", []), "heavy": ip in rules.get("heavy", []),
            "blocked": ip in rules.get("block", []), "util": met.get("util", 0),
            "congested": bool(met.get("congested"))}


def make_handler(server_ip):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _json(self, obj, status=200):
            body = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _fail(self, e, status=400):
            self._json({"ok": False, "code": e.code, "msg": e.msg}, status)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 16384:
                raise GatewayError("BAD_REQUEST", "too large")
            try:
                d = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                raise GatewayError("BAD_REQUEST", "bad JSON")
            if not isinstance(d, dict):
                raise GatewayError("BAD_REQUEST", "expected an object")
            return d

        def _session(self, sid):
            with SESSIONS_LOCK:
                s = SESSIONS.get(sid)
            if not s:
                raise GatewayError("NOT_LOGGED_IN", "Your session ended. Sign in again.")
            s.touched = time.time()
            return s

        def do_GET(self):
            u = urlparse(self.path)
            if u.path in ("/", "/index.html"):
                with open(PAGE, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            elif u.path == "/healthz":
                self._json({"ok": True, "sessions": len(SESSIONS), "server": server_ip})
            elif u.path == "/api/info":
                self._json({"server": server_ip, "ports": [PORT_CONTROL, PORT_ROOM, PORT_PM]})
            elif u.path == "/api/events":
                self._sse(parse_qs(u.query).get("sid", [""])[0])
            else:
                self._json({"ok": False, "code": "NOT_FOUND"}, 404)

        def _sse(self, sid):
            try:
                s = self._session(sid)
            except GatewayError as e:
                return self._fail(e, 401)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            try:
                self.wfile.write(b": ready\n\n")
                self.wfile.flush()
                while not s.closed:
                    try:
                        m = s.events.get(timeout=15)
                        self.wfile.write(b"data: " + json.dumps(m).encode() + b"\n\n")
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                    s.touched = time.time()
                    self.wfile.flush()
            except (OSError, ValueError):
                pass

        def do_POST(self):
            path = urlparse(self.path).path
            try:
                d = self._body()
                if path == "/api/login":
                    mode = d.get("mode")
                    user, pw = str(d.get("user", "")).strip(), str(d.get("password", ""))
                    if mode not in ("register", "login") or not user or not pw:
                        raise GatewayError("BAD_REQUEST", "Enter a name and a password.")
                    s = Session(server_ip)
                    s.login(mode, user, pw)
                    sid = secrets.token_hex(16)
                    with SESSIONS_LOCK:
                        SESSIONS[sid] = s
                    return self._json({"ok": True, "sid": sid, "user": user})
                s = self._session(str(d.get("sid", "")))
                if path == "/api/join":
                    room = str(d.get("room", "")).strip()
                    r = s.control("JOIN", room=room)
                    if room not in s.rooms:
                        s.rooms.append(room)
                    return self._json({"ok": True, "room": room, "members": r.get("members", [])})
                if path == "/api/leave":
                    room = str(d.get("room", ""))
                    s.control("LEAVE", room=room)
                    s.rooms = [x for x in s.rooms if x != room]
                    return self._json({"ok": True})
                if path == "/api/list":
                    what = d.get("what")
                    if what not in ("rooms", "users"):
                        raise GatewayError("BAD_REQUEST", "what must be rooms or users")
                    return self._json({"ok": True, "items": s.control("LIST", what=what).get("items", [])})
                if path == "/api/msg":
                    s.send(s.room_sock, "MSG_ROOM", room=str(d.get("room", "")), text=str(d.get("text", "")))
                    return self._json({"ok": True})
                if path == "/api/pm":
                    s.send(s.pm_sock, "MSG_PM", to=str(d.get("to", "")), text=str(d.get("text", "")))
                    return self._json({"ok": True})
                if path == "/api/net":
                    ip = s.ctl.getsockname()[0] if s.ctl else ""
                    return self._json(dict(network_view(ip), ok=True))
                if path == "/api/logout":
                    with SESSIONS_LOCK:
                        SESSIONS.pop(str(d.get("sid", "")), None)
                    s.close()
                    return self._json({"ok": True})
                self._json({"ok": False, "code": "NOT_FOUND"}, 404)
            except GatewayError as e:
                self._fail(e, 401 if e.code == "NOT_LOGGED_IN" else 400)

    return Handler


def serve(server_ip, host="0.0.0.0", port=8000):
    threading.Thread(target=reaper, daemon=True).start()
    httpd = ThreadingHTTPServer((host, port), make_handler(server_ip))
    httpd.daemon_threads = True
    return httpd


def main():
    global CONTROLLER
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--server", default="10.0.0.1", help="chat server address (default 10.0.0.1)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--controller", default=CONTROLLER, help="controller REST URL for the network card")
    a = ap.parse_args()
    CONTROLLER = a.controller
    httpd = serve(a.server, a.host, a.port)
    print(f"web chat on http://localhost:{a.port}  ->  chat server {a.server}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
