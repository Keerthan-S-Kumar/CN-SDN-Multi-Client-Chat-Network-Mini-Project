"""OWNER: B. Policy file handling (docs/interfaces.md §8). Pure python, no Ryu import."""
import json
import logging

log = logging.getLogger("policy")
KEYS = ("vip_users", "vip_rooms", "banned_users")


class Policy:
    def __init__(self, path=None):
        self.path = path
        self.data = {k: set() for k in KEYS}
        if path:
            self.load(path)

    def load(self, path=None):
        path = path or self.path
        try:
            with open(path) as f:
                self.update(json.load(f))
        except (OSError, ValueError) as e:
            log.warning("policy load failed (%s): %s", path, e)

    def update(self, d):
        """Replace the policy from a dict. Raises ValueError on a bad shape (state untouched)."""
        if not isinstance(d, dict):
            raise ValueError("policy must be an object")
        new = {}
        for k in KEYS:
            v = d.get(k, [])
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise ValueError("%s must be a list of strings" % k)
            new[k] = set(v)
        self.data = new

    def to_dict(self):
        return {k: sorted(self.data[k]) for k in KEYS}

    def is_banned(self, user):
        return user in self.data["banned_users"]

    def is_vip(self, user, rooms_of_user=()):
        return user in self.data["vip_users"] or any(r in self.data["vip_rooms"] for r in rooms_of_user)
