"""OWNER: B. REST routes (docs/interfaces.md §4, §8, §10). Handlers delegate to the app and return fast —
the chat server blocks on POST /event. Needs Ryu (imported by app.py only)."""
import json

from ryu.app.wsgi import ControllerBase, Response, route

APP_KEY = "chatsdn_app"


def _resp(code, obj):
    return Response(status=code, content_type="application/json", body=(json.dumps(obj) + "\n").encode())


class Api(ControllerBase):
    def __init__(self, req, link, data, **config):
        super().__init__(req, link, data, **config)
        self.app = data[APP_KEY]

    @route("event", "/event", methods=["POST"])
    def event(self, req, **kw):
        try:
            body = json.loads(req.body)
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
        except ValueError as e:
            return _resp(400, {"ok": False, "error": str(e)})
        code, obj = self.app.handle_event(body)
        return _resp(code, obj)

    @route("health", "/health", methods=["GET"])
    def health(self, req, **kw):
        return _resp(200, {"ok": True})

    @route("state", "/state", methods=["GET"])
    def state(self, req, **kw):
        return _resp(200, self.app.state())

    @route("metrics", "/metrics", methods=["GET"])
    def metrics(self, req, **kw):
        return _resp(200, self.app.metrics())

    @route("policy_get", "/policy", methods=["GET"])
    def policy_get(self, req, **kw):
        return _resp(200, self.app.policy.to_dict())

    @route("policy_set", "/policy", methods=["POST"])
    def policy_set(self, req, **kw):
        try:
            self.app.set_policy(json.loads(req.body))
        except ValueError as e:
            return _resp(400, {"ok": False, "error": str(e)})
        return _resp(200, {"ok": True})
