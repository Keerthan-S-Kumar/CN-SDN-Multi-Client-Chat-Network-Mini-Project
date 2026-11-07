"""OWNER: B. Dashboard routes: GET /ui (the page), GET /events (recent controller log), GET /snapshot.

Kept apart from rest_api.py and registered inside a try/except in app.py, so a problem here can never take
down the event API the chat server depends on. Same origin as /state and /metrics, so the page needs no CORS.
"""
import csv
import json
import os

from ryu.app.wsgi import ControllerBase, Response, route

APP_KEY = "chatsdn_app"
RESULTS_CSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eval", "results", "summary.csv")
LOCAL = ("127.0.0.1", "::1")
PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", "dashboard.html")


def tail_events(path, n=40, max_bytes=65536):
    """Last `n` parsed JSON lines of the controller log (oldest first). Missing file -> []."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            start = max(0, size - max_bytes)
            f.seek(start)
            data = f.read()
    except OSError:
        return []
    lines = data.split(b"\n")
    if start > 0:
        lines = lines[1:]                      # first line is probably cut in half
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out[-n:]


def read_results(path=RESULTS_CSV):
    """summary.csv -> list of dicts with numbers converted. Missing file -> []."""
    try:
        with open(path, newline="") as f:
            rows = list(csv.DictReader(f))
    except OSError:
        return []
    for r in rows:
        for k, v in list(r.items()):
            if k not in ("mode", "scenario"):
                try:
                    r[k] = float(v)
                except (TypeError, ValueError):
                    r[k] = None
    return rows


def _json(code, obj):
    return Response(status=code, content_type="application/json",
                    body=(json.dumps(obj) + "\n").encode(), headers=[("Cache-Control", "no-store")])


class UiApi(ControllerBase):
    def __init__(self, req, link, data, **config):
        super().__init__(req, link, data, **config)
        self.app = data[APP_KEY]

    @route("ui", "/ui", methods=["GET"])
    def page(self, req, **kw):
        try:
            with open(PAGE, "rb") as f:
                body = f.read()
        except OSError as e:
            return _json(500, {"ok": False, "error": "dashboard page missing: %s" % e})
        return Response(status=200, content_type="text/html; charset=UTF-8", body=body,
                        headers=[("Cache-Control", "no-store")])

    @route("events", "/events", methods=["GET"])
    def events(self, req, **kw):
        try:
            n = max(1, min(200, int(req.GET.get("n", "40"))))
        except ValueError:
            n = 40
        from controller import app as appmod          # resolved late so tests can patch the log path
        return _json(200, tail_events(appmod.CONTROLLER_LOG, n))

    @route("traffic", "/traffic", methods=["GET"])
    def traffic(self, req, **kw):
        return _json(200, self.app.traffic())

    @route("explain", "/explain", methods=["GET"])
    def explain(self, req, **kw):
        return _json(200, self.app.explain())

    @route("results", "/results", methods=["GET"])
    def results(self, req, **kw):
        return _json(200, read_results())

    @route("ui_action", "/ui/action", methods=["POST"])
    def action(self, req, **kw):
        """Admin buttons. Only from the machine the controller runs on: hosts in the network cannot use it."""
        if getattr(req, "remote_addr", "") not in LOCAL:
            return _json(403, {"ok": False, "error": "admin actions are only allowed from the controller machine"})
        try:
            body = json.loads(req.body)
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
        except ValueError as e:
            return _json(400, {"ok": False, "error": str(e)})
        code, obj = self.app.admin(body)
        return _json(code, obj)

    @route("snapshot", "/snapshot", methods=["GET"])
    def snapshot(self, req, **kw):
        return _json(200, self.app.snapshot())
