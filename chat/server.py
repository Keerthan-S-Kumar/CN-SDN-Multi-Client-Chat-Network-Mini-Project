"""OWNER: A. Threaded TCP chat server.

Follow docs/interfaces.md exactly:
  - listen on ports 5000-5003 (common.constants.CHAT_PORTS), 0.0.0.0
  - use chat.protocol.send_msg / recv_msg for framing
  - chat.notifier.notify() on register/login/join/leave/disconnect; WAIT on register/login before replying
  - D2: port 5003 BULK; spam.py -> flag/unflag; MAX_BAD_LOGINS bad logins from one IP -> block
  - a bad frame or abrupt disconnect must never crash the server
  - CLI: python3 chat/server.py --host 0.0.0.0
"""

if __name__ == "__main__":
    raise SystemExit("TODO (A): implement server")
