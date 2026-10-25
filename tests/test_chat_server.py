"""OWNER: C. End-to-end test of chat/server.py against the mock controller. No Mininet, no root.

Run:  PYTHONPATH=. python3 -m unittest tests.test_chat_server -v
Needs ports 5000-5003 and 18080 free on localhost (stop any running chat server first).
"""
import os
import socket
import threading
import time
import unittest
from http.server import HTTPServer

TEST_CONTROLLER_PORT = 18080
os.environ["CONTROLLER_URL"] = "http://127.0.0.1:%d" % TEST_CONTROLLER_PORT   # before importing the notifier

from chat import server as chat_server                                       # noqa: E402
from chat.protocol import recv_msg, send_msg                                  # noqa: E402
from common.constants import (MAX_BAD_LOGINS, PORT_BULK, PORT_CONTROL, PORT_PM,  # noqa: E402
                              PORT_ROOM)
from tools import mock_controller                                             # noqa: E402

HOST = "127.0.0.1"


class Conn:
    """Tiny blocking client: call() sends a request and returns the reply with the same id; pushes are queued."""

    def __init__(self, port, timeout=3.0):
        self.sock = socket.create_connection((HOST, port), timeout=timeout)
        self.next_id = 0
        self.pushes = []

    def call(self, type_, **fields):
        self.next_id += 1
        send_msg(self.sock, dict(id=self.next_id, type=type_, **fields))
        while True:
            m = recv_msg(self.sock)
            if m.get("id") == self.next_id:
                return m
            self.pushes.append(m)

    def push(self, timeout=3.0):
        if self.pushes:
            return self.pushes.pop(0)
        self.sock.settimeout(timeout)
        return recv_msg(self.sock)

    def close(self):
        self.sock.close()


class ChatServerTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.httpd = HTTPServer((HOST, TEST_CONTROLLER_PORT), mock_controller.H)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.state, cls.servers = chat_server.serve(HOST)

    @classmethod
    def tearDownClass(cls):
        for s in cls.servers:
            s.shutdown()
            s.server_close()
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        self.conns = []
        mock_controller.STATE.update({"registered": {}, "rooms": {}, "flagged": [], "blocked": []})

    def tearDown(self):
        for c in self.conns:
            try:
                c.close()
            except OSError:
                pass

    def conn(self, port=PORT_CONTROL):
        c = Conn(port)
        self.conns.append(c)
        return c

    def register(self, user, password="pw"):
        c = self.conn()
        r = c.call("REGISTER", user=user, password=password)
        self.assertEqual(r["type"], "OK", r)
        return c, r["token"]

    def attach(self, port, token):
        c = self.conn(port)
        r = c.call("ATTACH", token=token)
        self.assertEqual(r["type"], "OK", r)
        return c

    # ---- registration and login ---------------------------------------------
    def test_register_returns_token_and_notifies_controller_before_replying(self):
        _, token = self.register("alice")
        self.assertTrue(token)
        self.assertEqual(mock_controller.STATE["registered"].get(HOST), "alice")

    def test_duplicate_and_bad_names_are_rejected(self):
        self.register("dave")
        r = self.conn().call("REGISTER", user="dave", password="x")
        self.assertEqual((r["type"], r["code"]), ("ERR", "USER_EXISTS"))
        for bad in ("", "has space", "x" * 17, "bad!"):
            r = self.conn().call("REGISTER", user=bad, password="x")
            self.assertEqual((r["type"], r["code"]), ("ERR", "BAD_REQUEST"), bad)

    def test_login_checks_password_and_blocks_second_login(self):
        c, _ = self.register("erin", "secret")
        c2 = self.conn()
        r = c2.call("LOGIN", user="erin", password="wrong")
        self.assertEqual(r["code"], "BAD_CREDENTIALS")
        r = c2.call("LOGIN", user="erin", password="secret")      # still logged in on the first connection
        self.assertEqual(r["code"], "BAD_REQUEST")
        c.close()
        time.sleep(0.3)
        r = self.conn().call("LOGIN", user="erin", password="secret")
        self.assertEqual(r["type"], "OK", r)

    def test_unknown_user_login_and_not_logged_in(self):
        c = self.conn()
        self.assertEqual(c.call("LOGIN", user="nobody", password="x")["code"], "BAD_CREDENTIALS")
        self.assertEqual(c.call("JOIN", room="general")["code"], "NOT_LOGGED_IN")
        self.assertEqual(c.call("LIST", what="rooms")["code"], "NOT_LOGGED_IN")
        self.assertEqual(c.call("NONSENSE")["code"], "NOT_LOGGED_IN")

    def test_repeated_bad_logins_make_the_server_report_a_block(self):
        c = self.conn()
        for _ in range(MAX_BAD_LOGINS):
            c.call("LOGIN", user="ghost", password="x")
        self.assertIn(HOST, mock_controller.STATE["blocked"])

    # ---- rooms ----------------------------------------------------------------
    def test_join_leave_list(self):
        c, _ = self.register("fay")
        r = c.call("JOIN", room="lobby")
        self.assertEqual((r["type"], r["members"]), ("OK", ["fay"]))
        self.assertEqual(c.call("LIST", what="rooms")["items"], ["lobby"])
        self.assertIn("fay", c.call("LIST", what="users")["items"])
        self.assertEqual(c.call("LIST", what="bogus")["code"], "BAD_REQUEST")
        self.assertEqual(c.call("JOIN", room="bad room!")["code"], "BAD_REQUEST")
        self.assertEqual(c.call("LEAVE", room="lobby")["type"], "OK")
        self.assertEqual(c.call("LEAVE", room="lobby")["code"], "NOT_IN_ROOM")
        self.assertEqual(c.call("LIST", what="rooms")["items"], [])

    # ---- messaging --------------------------------------------------------------
    def test_room_message_reaches_other_members_only(self):
        a, ta = self.register("ann")
        b, tb = self.register("ben")
        o, to = self.register("oli")
        for c in (a, b):
            self.assertEqual(c.call("JOIN", room="chat")["type"], "OK")
        ra, rb, ro = (self.attach(PORT_ROOM, t) for t in (ta, tb, to))
        r = ra.call("MSG_ROOM", room="chat", text="hello", seq=7)
        self.assertEqual((r["type"], r["delivered"]), ("OK", 1))
        m = rb.push()
        self.assertEqual((m["type"], m["kind"], m["room"], m["from"], m["text"], m["seq"]),
                         ("MSG", "room", "chat", "ann", "hello", 7))
        self.assertIn("ts", m)
        self.assertEqual(ro.call("MSG_ROOM", room="chat", text="hi")["code"], "NOT_IN_ROOM")
        self.assertEqual(ra.call("MSG_ROOM", room="nowhere", text="hi")["code"], "NO_SUCH_ROOM")
        self.assertEqual(ra.call("MSG_ROOM", room="chat", text="")["code"], "BAD_REQUEST")
        ro.sock.settimeout(0.3)
        with self.assertRaises(socket.timeout):               # the outsider got nothing
            recv_msg(ro.sock)

    def test_private_message(self):
        a, ta = self.register("pat")
        b, tb = self.register("quin")
        rpa = self.attach(PORT_PM, ta)
        self.assertEqual(rpa.call("MSG_PM", to="quin", text="psst")["code"], "OFFLINE")
        self.assertEqual(rpa.call("MSG_PM", to="ghost", text="psst")["code"], "NO_SUCH_USER")
        rpb = self.attach(PORT_PM, tb)
        self.assertEqual(rpa.call("MSG_PM", to="quin", text="psst")["type"], "OK")
        m = rpb.push()
        self.assertEqual((m["kind"], m["from"], m["text"]), ("pm", "pat", "psst"))

    def test_chat_ports_reject_bad_token_and_wrong_first_frame(self):
        c = self.conn(PORT_ROOM)
        self.assertEqual(c.call("ATTACH", token="nope")["code"], "BAD_TOKEN")
        c2 = self.conn(PORT_PM)
        self.assertEqual(c2.call("MSG_PM", to="x", text="y")["code"], "BAD_REQUEST")

    def test_bulk(self):
        _, t = self.register("bob2")
        b = self.attach(PORT_BULK, t)
        self.assertEqual(b.call("BULK", size=1000)["type"], "OK")
        m = b.push()
        self.assertEqual((m["type"], len(m["data"])), ("BULK_DATA", 1000))
        self.assertEqual(b.call("BULK", size=10 ** 7)["code"], "BAD_REQUEST")
        self.assertEqual(b.call("BULK", size=0)["code"], "BAD_REQUEST")

    # ---- disconnect and robustness ---------------------------------------------
    def test_disconnect_cleans_up_and_notifies(self):
        a, ta = self.register("zed")
        a.call("JOIN", room="r1")
        ra = self.attach(PORT_ROOM, ta)
        self.assertEqual(mock_controller.STATE["registered"].get(HOST), "zed")
        a.close()
        time.sleep(0.4)
        self.assertNotIn(HOST, mock_controller.STATE["registered"])
        self.assertEqual(self.state.rooms.get("r1"), None)
        ra.sock.settimeout(2)
        with self.assertRaises((ConnectionError, OSError)):    # server closed the attached socket
            recv_msg(ra.sock)
        r = self.conn(PORT_ROOM).call("ATTACH", token=ta)      # token is no longer valid
        self.assertEqual(r["code"], "BAD_TOKEN")

    def test_garbage_does_not_kill_the_server(self):
        s = socket.create_connection((HOST, PORT_CONTROL), timeout=2)
        s.sendall(b"\x00\x00\x00\x05hello")                    # valid length, invalid JSON
        s.close()
        s = socket.create_connection((HOST, PORT_CONTROL), timeout=2)
        s.sendall(b"\xff\xff\xff\xff")                         # absurd length
        s.close()
        s = socket.create_connection((HOST, PORT_CONTROL), timeout=2)
        s.close()                                              # connect and hang up
        c = self.conn()
        r = c.call("REGISTER", user="survivor", password="x")
        self.assertEqual(r["type"], "OK", r)


if __name__ == "__main__":
    unittest.main()
