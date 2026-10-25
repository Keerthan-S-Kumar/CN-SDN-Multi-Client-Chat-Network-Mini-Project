#!/usr/bin/env python3
"""OWNER: A. Threaded TCP chat server (D1 scope: register/login, rooms, room + private messages, bulk).

Follows docs/interfaces.md:
  - listens on ports 5000-5003 (common.constants.CHAT_PORTS) on all interfaces
  - 4-byte length + JSON framing via chat.protocol.send_msg / recv_msg
  - 5000 control (REGISTER LOGIN JOIN LEAVE LIST), 5001 room chat, 5002 private messages, 5003 bulk
  - chat.notifier.notify() tells the controller about register/login/join/leave/disconnect; on
    register/login the server WAITS for the controller before replying, so the allow rule exists
    before the client opens its chat connections
  - a bad frame or an abrupt disconnect only ends that one connection, never the server

Run: python3 chat/server.py --host 0.0.0.0        (env CONTROLLER_URL overrides the controller address)
"""
import argparse
import hashlib
import hmac
import logging
import re
import secrets
import socket
import socketserver
import threading
import time

from chat.notifier import notify
from chat.protocol import recv_msg, send_msg
from common.constants import (CHAT_PORTS, MAX_BAD_LOGINS, MAX_BULK, PORT_BULK, PORT_CONTROL,
                              PORT_PM, PORT_ROOM)

log = logging.getLogger("server")

NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")
MAX_TEXT = 2000
MAX_PASSWORD = 64


class Peer:
    """One attached TCP connection (port 5001/5002) with a lock so replies and pushes never interleave."""

    def __init__(self, sock):
        self.sock = sock
        self.lock = threading.Lock()

    def send(self, obj):
        try:
            with self.lock:
                send_msg(self.sock, obj)
            return True
        except (OSError, ValueError):
            return False

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class Session:
    """State of one connection on the control port (5000)."""

    def __init__(self, ip):
        self.ip = ip
        self.user = None
        self.token = None
        self.post = None          # callable to run right after the reply is sent (e.g. a notify)


class State:
    """All shared server state, guarded by one lock. Never held across network calls."""

    def __init__(self):
        self.lock = threading.Lock()
        self.users = {}                       # name -> password hash
        self.tokens = {}                      # token -> name
        self.sessions = {}                    # name -> Session (online users)
        self.rooms = {}                       # room -> set(name)
        self.peers = {PORT_ROOM: {}, PORT_PM: {}}   # port -> {name: Peer}
        self.bad_logins = {}                  # ip -> consecutive bad logins


def pw_hash(user, password):
    return hashlib.sha256(("%s:%s" % (user, password)).encode("utf-8")).hexdigest()


def ok(rid, **fields):
    d = {"id": rid, "type": "OK"}
    d.update(fields)
    return d


def err(rid, code, msg=""):
    return {"id": rid, "type": "ERR", "code": code, "msg": msg}


def valid_name(s):
    return isinstance(s, str) and NAME_RE.match(s) is not None


class Handler(socketserver.BaseRequestHandler):
    port = None
    state = None

    # ---- entry point ------------------------------------------------------
    def handle(self):
        sock = self.request
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            if self.port == PORT_CONTROL:
                self.control(sock)
            elif self.port in (PORT_ROOM, PORT_PM):
                self.chat(sock)
            elif self.port == PORT_BULK:
                self.bulk(sock)
        except (ConnectionError, OSError):
            pass                              # peer went away
        except ValueError:
            pass                              # bad frame: drop this connection only
        except Exception:                     # never let one client take the server down
            log.exception("handler crashed on port %s", self.port)

    # ---- control port 5000 --------------------------------------------------
    def control(self, sock):
        sess = Session(self.client_address[0])
        try:
            while True:
                try:
                    req = recv_msg(sock)
                except ValueError:
                    send_msg(sock, err(None, "BAD_REQUEST", "bad frame"))
                    return
                send_msg(sock, self.dispatch(req, sess))
                if sess.post is not None:
                    post, sess.post = sess.post, None
                    post()
        finally:
            self.end_session(sess)

    def dispatch(self, req, sess):
        rid = req.get("id")
        kind = req.get("type")
        try:
            if kind == "REGISTER":
                return self.do_register(rid, req, sess)
            if kind == "LOGIN":
                return self.do_login(rid, req, sess)
            if sess.user is None:
                return err(rid, "NOT_LOGGED_IN", "register or log in first")
            if kind == "JOIN":
                return self.do_join(rid, req, sess)
            if kind == "LEAVE":
                return self.do_leave(rid, req, sess)
            if kind == "LIST":
                return self.do_list(rid, req)
            return err(rid, "BAD_REQUEST", "unknown type %r" % (kind,))
        except (KeyError, TypeError, AttributeError):
            return err(rid, "BAD_REQUEST", "malformed request")

    def start_session(self, sess, user):
        """Mark `user` online on this control connection. Caller holds state.lock."""
        st = self.state
        sess.user = user
        sess.token = secrets.token_hex(8)
        st.tokens[sess.token] = user
        st.sessions[user] = sess

    def do_register(self, rid, req, sess):
        user, password = req.get("user"), req.get("password")
        if not valid_name(user) or not isinstance(password, str) or not 1 <= len(password) <= MAX_PASSWORD:
            return err(rid, "BAD_REQUEST", "user must be 1-16 of [A-Za-z0-9_], password 1-64 chars")
        if sess.user is not None:
            return err(rid, "BAD_REQUEST", "already logged in on this connection")
        st = self.state
        with st.lock:
            if user in st.users:
                return err(rid, "USER_EXISTS", "name taken")
            st.users[user] = pw_hash(user, password)
            self.start_session(sess, user)
            token = sess.token
            st.bad_logins.pop(sess.ip, None)
        notify("register", user, sess.ip)             # wait for the controller before replying
        return ok(rid, token=token)

    def do_login(self, rid, req, sess):
        user, password = req.get("user"), req.get("password")
        if not valid_name(user) or not isinstance(password, str):
            return err(rid, "BAD_REQUEST", "bad user or password format")
        if sess.user is not None:
            return err(rid, "BAD_REQUEST", "already logged in on this connection")
        st = self.state
        block = False
        with st.lock:
            good = user in st.users and hmac.compare_digest(st.users[user], pw_hash(user, password))
            if not good:
                n = st.bad_logins.get(sess.ip, 0) + 1
                st.bad_logins[sess.ip] = n
                if n >= MAX_BAD_LOGINS:
                    st.bad_logins[sess.ip] = 0
                    block = True
            elif user in st.sessions:
                return err(rid, "BAD_REQUEST", "user is already logged in elsewhere")
            else:
                st.bad_logins.pop(sess.ip, None)
                self.start_session(sess, user)
                token = sess.token
        if not good:
            if block:
                notify("block", "", sess.ip, reason="bad_login")
            return err(rid, "BAD_CREDENTIALS", "wrong user or password")
        notify("login", user, sess.ip)
        return ok(rid, token=token)

    def do_join(self, rid, req, sess):
        room = req["room"]
        if not valid_name(room):
            return err(rid, "BAD_REQUEST", "room must be 1-16 of [A-Za-z0-9_]")
        st = self.state
        with st.lock:
            st.rooms.setdefault(room, set()).add(sess.user)
            members = sorted(st.rooms[room])
        sess.post = lambda: notify("join", sess.user, sess.ip, room=room)
        return ok(rid, members=members)

    def do_leave(self, rid, req, sess):
        room = req["room"]
        st = self.state
        with st.lock:
            members = st.rooms.get(room)
            if not members or sess.user not in members:
                return err(rid, "NOT_IN_ROOM", "you are not in that room")
            members.discard(sess.user)
            if not members:
                del st.rooms[room]
        sess.post = lambda: notify("leave", sess.user, sess.ip, room=room)
        return ok(rid)

    def do_list(self, rid, req):
        what = req.get("what")
        st = self.state
        with st.lock:
            if what == "rooms":
                return ok(rid, items=sorted(st.rooms))
            if what == "users":
                return ok(rid, items=sorted(st.sessions))
        return err(rid, "BAD_REQUEST", "what must be 'rooms' or 'users'")

    def end_session(self, sess):
        """Control connection closed: log the user out, drop their rooms and chat sockets, tell the controller."""
        if sess.user is None:
            return
        st = self.state
        to_close = []
        with st.lock:
            if st.sessions.get(sess.user) is sess:
                del st.sessions[sess.user]
            st.tokens.pop(sess.token, None)
            for room in list(st.rooms):
                st.rooms[room].discard(sess.user)
                if not st.rooms[room]:
                    del st.rooms[room]
            for port in (PORT_ROOM, PORT_PM):
                peer = st.peers[port].pop(sess.user, None)
                if peer is not None:
                    to_close.append(peer)
        for peer in to_close:
            peer.close()
        notify("disconnect", sess.user, sess.ip)

    # ---- chat ports 5001 (room) and 5002 (private) ---------------------------
    def attach(self, sock):
        """First frame must be ATTACH{token}. Returns (user, Peer) or None."""
        try:
            req = recv_msg(sock)
        except ValueError:
            send_msg(sock, err(None, "BAD_REQUEST", "bad frame"))
            return None
        rid = req.get("id")
        if req.get("type") != "ATTACH":
            send_msg(sock, err(rid, "BAD_REQUEST", "first frame must be ATTACH"))
            return None
        st = self.state
        with st.lock:
            user = st.tokens.get(req.get("token"))
            if user is None:
                send_msg(sock, err(rid, "BAD_TOKEN", "unknown or expired token"))
                return None
            peer = Peer(sock)
            old = st.peers[self.port].get(user)
            st.peers[self.port][user] = peer
        if old is not None and old is not peer:
            old.close()
        peer.send(ok(rid))
        return user, peer

    def chat(self, sock):
        res = self.attach(sock)
        if res is None:
            return
        user, peer = res
        try:
            while True:
                try:
                    req = recv_msg(sock)
                except ValueError:
                    peer.send(err(None, "BAD_REQUEST", "bad frame"))
                    return
                rid = req.get("id")
                try:
                    if self.port == PORT_ROOM and req.get("type") == "MSG_ROOM":
                        peer.send(self.do_msg_room(rid, req, user))
                    elif self.port == PORT_PM and req.get("type") == "MSG_PM":
                        peer.send(self.do_msg_pm(rid, req, user))
                    else:
                        peer.send(err(rid, "BAD_REQUEST", "unexpected type on this port"))
                except (KeyError, TypeError, AttributeError):
                    peer.send(err(rid, "BAD_REQUEST", "malformed request"))
        finally:
            st = self.state
            with st.lock:
                if st.peers[self.port].get(user) is peer:
                    del st.peers[self.port][user]
            peer.close()

    def do_msg_room(self, rid, req, user):
        room, text = req["room"], req["text"]
        if not isinstance(text, str) or not 1 <= len(text) <= MAX_TEXT:
            return err(rid, "BAD_REQUEST", "text must be 1-%d chars" % MAX_TEXT)
        st = self.state
        with st.lock:
            members = st.rooms.get(room)
            if members is None:
                return err(rid, "NO_SUCH_ROOM", "no such room")
            if user not in members:
                return err(rid, "NOT_IN_ROOM", "join the room first")
            targets = [st.peers[PORT_ROOM].get(m) for m in sorted(members) if m != user]
        push = {"type": "MSG", "kind": "room", "room": room, "from": user, "text": text, "ts": time.time()}
        if "seq" in req:
            push["seq"] = req["seq"]
        delivered = sum(1 for p in targets if p is not None and p.send(push))
        return ok(rid, delivered=delivered)

    def do_msg_pm(self, rid, req, user):
        to, text = req["to"], req["text"]
        if not isinstance(text, str) or not 1 <= len(text) <= MAX_TEXT:
            return err(rid, "BAD_REQUEST", "text must be 1-%d chars" % MAX_TEXT)
        st = self.state
        with st.lock:
            if not valid_name(to) or to not in st.users:
                return err(rid, "NO_SUCH_USER", "no such user")
            peer = st.peers[PORT_PM].get(to)
        if peer is None:
            return err(rid, "OFFLINE", "user is not online")
        push = {"type": "MSG", "kind": "pm", "from": user, "text": text, "ts": time.time()}
        if "seq" in req:
            push["seq"] = req["seq"]
        if not peer.send(push):
            return err(rid, "OFFLINE", "user is not online")
        return ok(rid)

    # ---- bulk port 5003 ------------------------------------------------------
    def bulk(self, sock):
        """After ATTACH, BULK{size} -> OK then one BULK_DATA frame of `size` bytes (low-priority class traffic)."""
        try:
            req = recv_msg(sock)
        except ValueError:
            send_msg(sock, err(None, "BAD_REQUEST", "bad frame"))
            return
        rid = req.get("id")
        st = self.state
        with st.lock:
            user = st.tokens.get(req.get("token")) if req.get("type") == "ATTACH" else None
        if user is None:
            send_msg(sock, err(rid, "BAD_TOKEN", "unknown or expired token"))
            return
        send_msg(sock, ok(rid))
        while True:
            try:
                req = recv_msg(sock)
            except ValueError:
                send_msg(sock, err(None, "BAD_REQUEST", "bad frame"))
                return
            rid = req.get("id")
            size = req.get("size")
            if req.get("type") != "BULK" or not isinstance(size, int) or isinstance(size, bool) \
                    or not 0 < size <= MAX_BULK:
                send_msg(sock, err(rid, "BAD_REQUEST", "BULK{size} with 0 < size <= %d" % MAX_BULK))
                continue
            send_msg(sock, ok(rid))
            send_msg(sock, {"type": "BULK_DATA", "data": "x" * size})


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 128


def make_server(host, port, state):
    handler = type("Handler%d" % port, (Handler,), {"port": port, "state": state})
    return Server((host, port), handler)


def serve(host="0.0.0.0", ports=CHAT_PORTS):
    """Start one listener per port in background threads. Returns (state, [servers])."""
    state = State()
    servers = [make_server(host, p, state) for p in ports]
    for s in servers:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    return state, servers


def main():
    ap = argparse.ArgumentParser(description="Multi-client chat server")
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    state, servers = serve(args.host)
    log.info("listening on %s ports %s", args.host, ", ".join(str(p) for p in CHAT_PORTS))
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        for s in servers:
            s.shutdown()


if __name__ == "__main__":
    main()
