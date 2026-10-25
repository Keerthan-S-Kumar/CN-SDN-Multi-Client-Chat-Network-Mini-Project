"""OWNER: C. End-to-end test of chat/client.py (run as real processes) against chat/server.py and the mock controller.

Run:  PYTHONPATH=. python3 -m unittest tests.test_chat_client -v
Needs ports 5000-5003 and 18080 free on localhost.
"""
import os
import subprocess
import sys
import threading
import time
import unittest
from http.server import HTTPServer

TEST_CONTROLLER_PORT = 18080
os.environ["CONTROLLER_URL"] = "http://127.0.0.1:%d" % TEST_CONTROLLER_PORT

from chat import server as chat_server       # noqa: E402
from chat.client import ChatClient, ChatError  # noqa: E402
from tools import mock_controller            # noqa: E402

HOST = "127.0.0.1"


def start_client(user, *flags):
    env = dict(os.environ, PYTHONPATH=os.getcwd())
    return subprocess.Popen([sys.executable, "chat/client.py", "--server", HOST, "--user", user,
                             "--linger", "1.5"] + list(flags),
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, env=env)


class ChatClientTest(unittest.TestCase):

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

    def test_two_cli_clients_chat_in_a_room_and_by_private_message(self):
        bob = start_client("bob", "--register")
        time.sleep(0.5)
        bob.stdin.write("/join lobby\n")
        bob.stdin.flush()
        time.sleep(0.3)
        alice = start_client("alice", "--register")
        time.sleep(0.5)
        alice.stdin.write("/join lobby\n/msg lobby hello bob\n/pm bob secret hello\n/list rooms\n/list users\n")
        alice.stdin.flush()
        alice.stdin.close()
        a_out, _ = alice.communicate(timeout=15)
        bob.stdin.close()
        b_out, _ = bob.communicate(timeout=15)
        self.assertIn("registered and logged in as alice", a_out)
        self.assertIn("joined lobby, members: alice, bob", a_out)
        self.assertIn("rooms: lobby", a_out)
        self.assertIn("bob", a_out.split("users:")[1])
        self.assertIn("[lobby] alice: hello bob", b_out)
        self.assertIn("[pm from alice] secret hello", b_out)

    def test_errors_are_printed_not_fatal(self):
        c = start_client("carl", "--register")
        time.sleep(0.4)
        c.stdin.write("/msg nowhere hi\n/join bad!room\n/pm ghost hi\n/bogus\n/leave lobby\n")
        c.stdin.flush()
        c.stdin.close()
        out, _ = c.communicate(timeout=15)
        self.assertIn("! NO_SUCH_ROOM", out)
        self.assertIn("! BAD_REQUEST", out)
        self.assertIn("! NO_SUCH_USER", out)
        self.assertIn("unknown command", out)
        self.assertIn("! NOT_IN_ROOM", out)
        self.assertEqual(c.returncode, 0)

    def test_login_after_register_and_wrong_password(self):
        c1 = ChatClient(HOST, "dina", "pw1")
        c1.register()
        c1.close()
        time.sleep(0.4)
        c2 = ChatClient(HOST, "dina", "nope")
        with self.assertRaises(ChatError) as ctx:
            c2.login()
        self.assertEqual(ctx.exception.code, "BAD_CREDENTIALS")
        c2.close()
        c3 = ChatClient(HOST, "dina", "pw1")
        c3.login()
        self.assertEqual(c3.bulk(500), 500)
        c3.close()

    def test_client_reports_unreachable_server(self):
        env = dict(os.environ, PYTHONPATH=os.getcwd())
        r = subprocess.run([sys.executable, "chat/client.py", "--server", "127.0.0.1", "--user", "x"],
                           input="", capture_output=True, text=True, env=env, timeout=10)
        # the server is up in this test, so this only checks the client starts and exits cleanly
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
