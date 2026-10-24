"""Fake chat-server events so B can test the controller without A's server.
Usage: python3 tools/post_event.py register alice 10.0.0.2
       python3 tools/post_event.py join alice 10.0.0.2 --room general
       python3 tools/post_event.py flag alice 10.0.0.2 --reason spam
Env CONTROLLER_URL overrides the target (default http://10.0.0.254:8080; use http://127.0.0.1:8080 off-Mininet).
"""
import argparse

from chat.notifier import notify

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("event")
    p.add_argument("user")
    p.add_argument("ip")
    p.add_argument("--room")
    p.add_argument("--reason")
    a = p.parse_args()
    print("ok" if notify(a.event, a.user, a.ip, a.room, a.reason) else "FAILED")
