"""OWNER: C. D1 topology test: the `single` topology matches docs/interfaces.md section 1.

Needs root and an OpenFlow 1.3 controller already listening on 127.0.0.1:6653 (anything that
forwards, e.g. `ryu-manager ryu.app.simple_switch_13`, or B's controller). If no controller
is found the whole class is skipped, not failed.

Run:  sudo env PYTHONPATH=. python3 -m unittest tests.test_topology -v
Order: sudo mn -c   ->   start the controller   ->   run this test.
"""
import json
import os
import threading
import unittest
from http.server import HTTPServer

from common import constants as C
from tools.mock_controller import H          # REST stand-in; bound on 0.0.0.0 here so hosts can reach it
from topology.topo import PREFIX, build


@unittest.skipUnless(os.geteuid() == 0, "needs root: run with sudo")
class SingleTopologyTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.net, cls.root, cls.root_link = build(C.TOPO_SINGLE)
        cls.httpd = None
        try:
            cls.net.start()
            cls.root.setIP("%s/%d" % (C.CONTROLLER_MGMT_IP, PREFIX), intf=cls.root_link.intf1)
            if not cls.net.waitConnected(timeout=20):
                raise unittest.SkipTest("no OpenFlow controller on %s:%d - start one first"
                                        % (C.OF_IP, C.OF_PORT))
            cls.net.pingAll(timeout=2)           # warm-up: lets the controller learn hosts
            cls.httpd = HTTPServer(("0.0.0.0", C.CONTROLLER_REST_PORT), H)
            threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        except BaseException:
            cls._shutdown()
            raise

    @classmethod
    def _shutdown(cls):
        if cls.httpd is not None:
            cls.httpd.shutdown()
            cls.httpd.server_close()
        cls.net.stop()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown()

    def test_host_addresses_match_contract(self):
        for name in ("h1", "h2", "h3", "h4"):
            h = self.net.get(name)
            want_ip = C.SERVER_IP if name == "h1" else C.CLIENT_IPS[name]
            self.assertEqual(h.IP(), want_ip, name)
            self.assertEqual(h.MAC().lower(), C.HOST_MAC[name], name)

    def test_switch_port_numbers_match_contract(self):
        s1 = self.net.get("s1")
        for who, port in C.SINGLE_PORTS.items():
            intf = s1.intfs[port]
            link = intf.link
            other = link.intf1 if link.intf2 is intf else link.intf2
            self.assertEqual(other.node.name, who, "port %d" % port)

    def test_all_hosts_reach_each_other(self):
        self.assertEqual(self.net.pingAll(timeout=2), 0)

    def test_every_host_reaches_controller_address(self):
        for name in ("h1", "h2", "h3", "h4"):
            out = self.net.get(name).cmd("ping -c 1 -W 2 %s" % C.CONTROLLER_MGMT_IP)
            self.assertIn("1 received", out, "%s: %s" % (name, out))

    def test_rest_health_reachable_through_the_network(self):
        out = self.net.get("h2").cmd("curl -s -m 3 http://%s:%d/health"
                                     % (C.CONTROLLER_MGMT_IP, C.CONTROLLER_REST_PORT))
        self.assertTrue(json.loads(out)["ok"], out)


if __name__ == "__main__":
    unittest.main()
