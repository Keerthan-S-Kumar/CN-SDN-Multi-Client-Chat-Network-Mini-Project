"""OWNER: B. ryu.app.wsgi routes: POST /event, GET /health /state /metrics /policy, POST /policy
(docs/interfaces.md sections 4, 8, 10). Handlers return fast: the chat server blocks on POST /event.
All logic lives in the controller app (self.app); this file only translates HTTP <-> Python.
"""
import json

from ryu.app.wsgi import ControllerBase, Response, route


def _json(obj, status=200):
    return Response(content_type="application/json", body=json.dumps(obj).encode("utf-8"), status=status)


class ChatRest(ControllerBase):

    def __init__(self, req, link, data, **config):
        super(ChatRest, self).__init__(req, link, data, **config)
        self.app = data["app"]

    @route("chat_health", "/health", methods=["GET"])
    def health(self, req, **kwargs):
        return _json({"ok": True, "mode": self.app.mode})

    @route("chat_state", "/state", methods=["GET"])
    def state(self, req, **kwargs):
        return _json(self.app.state_view())

    @route("chat_metrics", "/metrics", methods=["GET"])
    def metrics(self, req, **kwargs):
        return _json(self.app.metrics_view())

    @route("chat_policy_get", "/policy", methods=["GET"])
    def get_policy(self, req, **kwargs):
        return _json(self.app.policy.data)

    @route("chat_policy_set", "/policy", methods=["POST"])
    def set_policy(self, req, **kwargs):
        try:
            self.app.policy.set(json.loads(req.body.decode("utf-8")))
        except (ValueError, UnicodeDecodeError) as e:
            return _json({"ok": False, "error": str(e)}, 400)
        return _json({"ok": True})

    @route("chat_event", "/event", methods=["POST"])
    def event(self, req, **kwargs):
        try:
            body = json.loads(req.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            return _json({"ok": False, "error": "bad json: %s" % e}, 400)
        status, reply = self.app.handle_event(body)
        return _json(reply, status)
