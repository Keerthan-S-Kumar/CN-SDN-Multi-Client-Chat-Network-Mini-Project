"""OWNER: B. Loads controller/policy.json (docs/interfaces.md section 8); is_vip(user, rooms), is_banned(user)."""
import json
import os

POLICY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "policy.json")
DEFAULT = {"vip_users": [], "vip_rooms": [], "banned_users": []}


class Policy:
    def __init__(self, path=POLICY_FILE):
        self.path = path
        self.data = dict(DEFAULT)
        self.load()

    def load(self):
        try:
            with open(self.path) as f:
                self.set(json.load(f))
        except (OSError, ValueError):
            self.data = dict(DEFAULT)

    def set(self, d):
        """Replace the policy. Raises ValueError if the document is malformed."""
        if not isinstance(d, dict):
            raise ValueError("policy must be a JSON object")
        new = {}
        for key in DEFAULT:
            val = d.get(key, [])
            if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
                raise ValueError("%s must be a list of strings" % key)
            new[key] = list(val)
        self.data = new

    def is_vip(self, user, rooms=()):
        return user in self.data["vip_users"] or any(r in self.data["vip_rooms"] for r in rooms)

    def is_banned(self, user):
        return user in self.data["banned_users"]
