"""OWNER: A. Sliding-window spam detector used by server.py.
SpamDetector.record(user, now) -> True when >= SPAM_MSGS messages in SPAM_WINDOW s (flag);
SpamDetector.quiet(user, now) -> True after SPAM_QUIET s without messages (unflag)."""
# TODO (A)
