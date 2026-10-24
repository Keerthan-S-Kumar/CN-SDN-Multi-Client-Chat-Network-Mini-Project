"""Stand-in controller REST API so A can test the chat server without Ryu/Mininet.
Run: python3 tools/mock_controller.py   (listens on 127.0.0.1:8080)
Then: CONTROLLER_URL=http://127.0.0.1:8080 python3 chat/server.py
"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

from common.constants import EVENTS

STATE = {"registered": {}, "rooms": {}, "flagged": [], "blocked": []}


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True})
        elif self.path == "/state":
            self._send(200, STATE)
        elif self.path == "/metrics":
            self._send(200, {"mode": "mock", "congested": False, "util": 0.0, "throttled": STATE["flagged"],
                             "heavy": [], "blocked": STATE["blocked"], "links_down": [], "drop_hits": 0,
                             "last_flow_install_ms": 0.0, "last_reroute_ms": 0.0})
        else:
            self._send(404, {"ok": False})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        try:
            e = json.loads(self.rfile.read(n))
            assert e["event"] in EVENTS and "ip" in e and "user" in e
        except Exception as ex:
            return self._send(400, {"ok": False, "error": str(ex)})
        print("EVENT", e, flush=True)
        ev, ip = e["event"], e["ip"]
        if ev in ("register", "login"):
            STATE["registered"][ip] = e["user"]
        elif ev == "disconnect":
            STATE["registered"].pop(ip, None)
        elif ev == "join":
            STATE["rooms"].setdefault(e["room"], []).append(e["user"])
        elif ev == "leave":
            m = STATE["rooms"].get(e["room"], [])
            if e["user"] in m:
                m.remove(e["user"])
        elif ev == "flag" and ip not in STATE["flagged"]:
            STATE["flagged"].append(ip)
        elif ev == "unflag" and ip in STATE["flagged"]:
            STATE["flagged"].remove(ip)
        elif ev == "block" and ip not in STATE["blocked"]:
            STATE["blocked"].append(ip)
        self._send(200, {"ok": True})


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8080), H).serve_forever()
