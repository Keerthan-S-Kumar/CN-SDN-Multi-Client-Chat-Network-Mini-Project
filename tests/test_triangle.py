"""OWNER: C. D2 triangle structure + QoS test. Needs root, but NO controller: it only checks what the
topology and `ovs-vsctl` configure (ports, links, addresses, HTB queues), not forwarding.

Run:  sudo env PYTHONPATH=. python3 -m unittest tests.test_triangle -v
Run `sudo mn -c` first. A controller may be running or not; it makes no difference here.
"""
import os
import re
import subprocess
import unittest

from common import constants as C
from topology import qos
from topology.topo import PREFIX, build


def other_end(intf):
    link = intf.link
    return link.intf1 if link.intf2 is intf else link.intf2


def vsctl(*args):
    r = subprocess.run(["ovs-vsctl"] + list(args), capture_output=True, text=True)
    return r.stdout.strip()


@unittest.skipUnless(os.geteuid() == 0, "needs root: run with sudo")
class TriangleStructureTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.net, cls.root, cls.root_link = build(C.TOPO_TRIANGLE)
        try:
            cls.net.start()
            cls.root.setIP("%s/%d" % (C.CONTROLLER_MGMT_IP, PREFIX), intf=cls.root_link.intf1)
            qos.setup_qos()
        except BaseException:
            cls._shutdown()
            raise

    @classmethod
    def _shutdown(cls):
        try:
            qos.clear_qos()
        finally:
            cls.net.stop()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown()

    def sw(self, dpid):
        return self.net.get("s%d" % dpid)

    def test_host_addresses_match_contract(self):
        for name in ("h1", "h2", "h3", "h4"):
            h = self.net.get(name)
            want_ip = C.SERVER_IP if name == "h1" else C.CLIENT_IPS[name]
            self.assertEqual(h.IP(), want_ip, name)
            self.assertEqual(h.MAC().lower(), C.HOST_MAC[name], name)

    def test_switch_dpids(self):
        for n in (1, 2, 3):
            self.assertEqual(int(self.sw(n).dpid, 16), n)

    def test_host_ports_match_contract(self):
        for host, (dpid, port) in C.TRIANGLE_HOST_PORTS.items():
            self.assertEqual(other_end(self.sw(dpid).intfs[port]).node.name, host,
                             "%s should be on s%d port %d" % (host, dpid, port))

    def test_inter_switch_links_match_contract(self):
        for a, pa, b, pb in C.TRIANGLE_LINKS:
            end = other_end(self.sw(a).intfs[pa])
            self.assertEqual(end.node.name, "s%d" % b, "s%d:%d" % (a, pa))
            self.assertIs(end, self.sw(b).intfs[pb], "s%d:%d <-> s%d:%d" % (a, pa, b, pb))

    def test_root_port_on_s1(self):
        dpid, port = C.TRIANGLE_ROOT_PORT
        self.assertEqual(other_end(self.sw(dpid).intfs[port]).node.name, "root")
        self.assertEqual(self.root_link.intf1.MAC().lower(), C.CONTROLLER_MGMT_MAC)

    def test_edge_ports_are_exactly_the_non_link_ports(self):
        used = {(a, pa) for a, pa, b, pb in C.TRIANGLE_LINKS} | \
               {(b, pb) for a, pa, b, pb in C.TRIANGLE_LINKS}
        for dpid in (1, 2, 3):
            edge = set(self.sw(dpid).intfs) - {p for d, p in used if d == dpid} - {0}
            want = {p for d, p in C.TRIANGLE_HOST_PORTS.values() if d == dpid}
            if dpid == C.TRIANGLE_ROOT_PORT[0]:
                want.add(C.TRIANGLE_ROOT_PORT[1])
            self.assertEqual(edge, want, "edge ports of s%d" % dpid)

    def test_qos_is_htb_with_the_bottleneck_rate(self):
        port = qos.port_name()
        self.assertNotEqual(vsctl("get", "port", port, "qos"), "[]", "no QoS on %s" % port)
        self.assertEqual(vsctl("--bare", "--columns=type", "find", "qos"), "linux-htb")
        self.assertIn(str(C.BOTTLENECK_BPS), vsctl("--bare", "--columns=other_config", "find", "qos"))

    def test_four_queues_with_contract_parameters(self):
        self.assertEqual(len(vsctl("--bare", "--columns=_uuid", "find", "queue").split()), 4)
        listing = vsctl("list", "queue")
        for q, (mn, mx, prio) in C.QUEUE_PARAMS.items():
            for key, val in (("min-rate", mn), ("max-rate", mx), ("priority", prio)):
                self.assertRegex(listing, r'%s="?%d"?' % (key, val), "queue %d: %s" % (q, key))

    def test_kernel_has_htb_classes_on_the_bottleneck_port(self):
        out = subprocess.run(["tc", "class", "show", "dev", qos.port_name()],
                             capture_output=True, text=True).stdout
        self.assertGreaterEqual(out.count("htb"), 4, out)

    def test_clear_then_setup_again(self):
        qos.clear_qos()
        self.assertEqual(vsctl("get", "port", qos.port_name(), "qos"), "[]")
        self.assertEqual(vsctl("--bare", "--columns=_uuid", "find", "queue"), "")
        qos.setup_qos()
        self.assertNotEqual(vsctl("get", "port", qos.port_name(), "qos"), "[]")


if __name__ == "__main__":
    unittest.main()
