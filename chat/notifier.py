"""Server -> controller event hook. Part of the frozen contract (docs/interfaces.md §4)."""
import json
import logging
import time
import urllib.request

from common.constants import CONTROLLER_URL, EVENTS

log = logging.getLogger("notifier")


def notify(event, user, ip, room=None, reason=None):
    """POST one event to the controller. Blocking, 1 s timeout, never raises. Returns True on success."""
    assert event in EVENTS, event
    body = {"event": event, "user": user, "ip": ip, "ts": time.time()}
    if room is not None:
        body["room"] = room
    if reason is not None:
        body["reason"] = reason
    req = urllib.request.Request(
        CONTROLLER_URL + "/event", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=1.0) as r:
            return r.status == 200
    except Exception as e:  # controller down must not break chat
        log.warning("controller notify failed (%s): %s", event, e)
        return False
