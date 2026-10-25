"""OWNER: A. Sliding-window spam detector used by server.py.
SpamDetector.record(user, now) -> True when >= SPAM_MSGS messages in SPAM_WINDOW s (flag);
SpamDetector.quiet(user, now) -> True after SPAM_QUIET s without messages (unflag)."""
import threading
from collections import deque

from common.constants import SPAM_MSGS, SPAM_WINDOW, SPAM_QUIET


class SpamDetector:
    def __init__(self):
        self._lock = threading.Lock()
        self._times = {}        # user -> deque of recent message times
        self._last = {}         # user -> time of last message
        self._flagged = set()

    def record(self, user, now):
        """Note one message. True only when this message makes the user newly flagged."""
        with self._lock:
            q = self._times.setdefault(user, deque())
            q.append(now)
            while q and now - q[0] > SPAM_WINDOW:
                q.popleft()
            self._last[user] = now
            if len(q) >= SPAM_MSGS and user not in self._flagged:
                self._flagged.add(user)
                return True
            return False

    def quiet(self, user, now):
        """True (once) when a flagged user has sent nothing for SPAM_QUIET seconds."""
        with self._lock:
            if user in self._flagged and now - self._last.get(user, 0) >= SPAM_QUIET:
                self._flagged.discard(user)
                self._times.pop(user, None)
                return True
            return False

    def flagged_users(self):
        with self._lock:
            return list(self._flagged)

    def forget(self, user):
        """Drop all state for a user (on disconnect). True if they were flagged."""
        with self._lock:
            was = user in self._flagged
            self._flagged.discard(user)
            self._times.pop(user, None)
            self._last.pop(user, None)
            return was