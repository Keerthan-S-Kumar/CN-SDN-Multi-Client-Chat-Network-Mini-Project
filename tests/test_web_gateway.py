"""Web gateway test: real chat server (subprocess) + real gateway, driven over HTTP like a browser.
Needs ports 5000-5003 free. Run: python3 -m pytest tests/test_web_gateway.py -v"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from chat import web_gateway  # noqa: E402


def post(base, path, **body):
    req = urllib.request.Request(base + "/api/" + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return json.load(e)


def read_events(base, sid, want, timeout=5):
    """Read the SSE stream until `want(event)` is true."""
    r = urllib.request.urlopen(base + "/api/events?sid=" + sid, timeout=timeout)
    end = time.time() + timeout
    while time.time() < end:
        line = r.readline().decode()
        if line.startswith("data: "):
            m = json.loads(line[6:])
            if want(m):
                return m
    return None


@pytest.fixture(scope="module")
def base():
    env = dict(os.environ, PYTHONPATH=ROOT, CONTROLLER_URL="http://127.0.0.1:9")
    srv = subprocess.Popen([sys.executable, os.path.join(ROOT, "chat", "server.py"), "--host", "127.0.0.1"], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    httpd = web_gateway.serve("127.0.0.1", "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % httpd.server_address[1]
    httpd.shutdown()
    srv.terminate()


def test_page_served(base):
    html = urllib.request.urlopen(base + "/").read().decode()
    assert "<title>Chat network</title>" in html


def test_bad_login_and_duplicate(base):
    assert post(base, "login", mode="login", user="nobody_w", password="x")["code"] == "BAD_CREDENTIALS"
    assert post(base, "login", mode="register", user="dup_w", password="pw")["ok"]
    assert post(base, "login", mode="register", user="dup_w", password="pw")["code"] == "USER_EXISTS"


def test_room_and_private_messages(base):
    a = post(base, "login", mode="register", user="ann_w", password="pw")["sid"]
    b = post(base, "login", mode="register", user="ben_w", password="pw")["sid"]
    assert post(base, "join", sid=a, room="lobby_w")["ok"]
    assert "ann_w" in post(base, "join", sid=b, room="lobby_w")["members"]
    assert {"ann_w", "ben_w"} <= set(post(base, "list", sid=a, what="users")["items"])
    assert "lobby_w" in post(base, "list", sid=a, what="rooms")["items"]
    assert post(base, "msg", sid=a, room="lobby_w", text="hello room")["ok"]
    m = read_events(base, b, lambda m: m.get("type") == "MSG")
    assert (m["kind"], m["from"], m["text"]) == ("room", "ann_w", "hello room")
    assert post(base, "pm", sid=b, to="ann_w", text="psst")["ok"]
    m = read_events(base, a, lambda m: m.get("kind") == "pm")
    assert (m["from"], m["text"]) == ("ben_w", "psst")


def test_errors_arrive_as_events(base):
    s = post(base, "login", mode="register", user="eve_w", password="pw")["sid"]
    post(base, "msg", sid=s, room="never_joined_w", text="x")
    m = read_events(base, s, lambda m: m.get("type") == "ERR")
    assert m["code"] in ("NOT_IN_ROOM", "NO_SUCH_ROOM")


def test_logout_ends_session(base):
    s = post(base, "login", mode="register", user="gone_w", password="pw")["sid"]
    assert post(base, "logout", sid=s)["ok"]
    assert post(base, "list", sid=s, what="users")["code"] == "NOT_LOGGED_IN"


def test_network_card_without_and_with_controller(base):
    s = post(base, "login", mode="register", user="net_w", password="pw")["sid"]
    old = web_gateway.CONTROLLER
    try:
        web_gateway.CONTROLLER = "http://127.0.0.1:9"          # nothing listens: card must degrade, not fail
        n = post(base, "net", sid=s)
        assert n["ok"] and n["available"] is False

        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = json.dumps({"/snapshot": {"rules": {"allow": ["127.0.0.1"], "vip": ["127.0.0.1"]}},
                                   "/metrics": {"mode": "sdn", "util": 0.4, "congested": False}}[self.path]).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        fake = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=fake.serve_forever, daemon=True).start()
        web_gateway.CONTROLLER = "http://127.0.0.1:%d" % fake.server_address[1]
        n = post(base, "net", sid=s)
        fake.shutdown()
        assert n["available"] and n["allowed"] and n["vip"] and not n["blocked"] and n["util"] == 0.4
    finally:
        web_gateway.CONTROLLER = old
