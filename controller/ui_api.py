"""OWNER: B. Dashboard routes: GET /ui (the page), GET /events (recent controller log), GET /snapshot.

Kept apart from rest_api.py and registered inside a try/except in app.py, so a problem here can never take
down the event API the chat server depends on. Same origin as /state and /metrics, so the page needs no CORS.
"""
import json
import os

from ryu.app.wsgi import ControllerBase, Response, route

APP_KEY = "chatsdn_app"
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

    @route("snapshot", "/snapshot", methods=["GET"])
    def snapshot(self, req, **kw):
        return _json(200, self.app.snapshot())
