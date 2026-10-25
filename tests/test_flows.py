"""OWNER: C. Flow-rule tests: build every FlowMod in controller/flows.py, serialize it to real OpenFlow 1.3
bytes, decode it again and check the fields against docs/interfaces.md section 5. No switch, no root.

Needs Ryu importable, so run it with the Ryu environment's Python (the system Python 3.14 skips it):
  source ~/ryu-env/bin/activate && PYTHONPATH=. python -m unittest tests.test_flows -v
"""
import unittest

try:
    from ryu.ofproto import ofproto_parser, ofproto_v1_3, ofproto_v1_3_parser
    HAVE_RYU = True
except Exception:                      # pragma: no cover
    HAVE_RYU = False

from common import constants as C

if HAVE_RYU:
    from controller import flows


class FakeDP:
    """Just enough of a Ryu Datapath: collects what would be sent, as decoded OpenFlow messages."""

    def __init__(self, dpid=1):
        self.id = dpid
        self.ofproto = ofproto_v1_3
        self.ofproto_parser = ofproto_v1_3_parser
        self.xid = 0
        self.sent = []

    def set_xid(self, msg):
        self.xid += 1
        msg.set_xid(self.xid)
        return self.xid

    def send_msg(self, msg):
        if msg.xid is None:
            self.set_xid(msg)
        msg.serialize()
        if isinstance(msg, ofproto_v1_3_parser.OFPPacketOut):   # Ryu has no decoder for packet-out: keep the object
            self.sent.append(msg)
            return
        version, msg_type, msg_len, xid = ofproto_parser.header(bytes(msg.buf))
        self.sent.append(ofproto_parser.msg(self, version, msg_type, msg_len, xid, bytes(msg.buf)))


def mods(dp):
    return [m for m in dp.sent if isinstance(m, ofproto_v1_3_parser.OFPFlowMod)]


def actions_of(mod):
    out = []
    for inst in mod.instructions:
        if isinstance(inst, ofproto_v1_3_parser.OFPInstructionActions):
            out.extend(inst.actions)
    return out


def goto_of(mod):
    for inst in mod.instructions:
        if isinstance(inst, ofproto_v1_3_parser.OFPInstructionGotoTable):
            return inst.table_id
    return None


@unittest.skipUnless(HAVE_RYU, "Ryu not importable here: run with ~/ryu-env/bin/python")
class FlowTest(unittest.TestCase):

    def find(self, dp, **want):
        """The decoded FlowMods whose fields equal `want` (priority, table_id, cookie, command)."""
        return [m for m in mods(dp) if all(getattr(m, k) == v for k, v in want.items())]

    def test_defaults_sdn(self):
        dp = FakeDP()
        flows.install_defaults(dp, "sdn")
        ms = mods(dp)
        self.assertEqual(ms[0].command, dp.ofproto.OFPFC_DELETE)                    # clean slate first
        self.assertEqual(ms[0].table_id, dp.ofproto.OFPTT_ALL)
        t0 = self.find(dp, table_id=0, priority=C.PRIO_DEFAULT)[0]
        self.assertEqual(goto_of(t0), 1)
        t1 = self.find(dp, table_id=1, priority=C.PRIO_DEFAULT)[0]
        self.assertEqual(actions_of(t1)[0].port, dp.ofproto.OFPP_CONTROLLER)
        drops = self.find(dp, table_id=0, priority=C.PRIO_DROP)
        self.assertEqual(sorted(m.match["tcp_dst"] for m in drops), [5001, 5002, 5003])
        for m in drops:
            self.assertEqual(m.instructions, [])                                     # no instructions = drop
            self.assertEqual(m.match["eth_type"], 0x0800)
            self.assertEqual(m.match["ip_proto"], 6)
            self.assertEqual(m.cookie, C.COOKIE_ALLOW + (m.match["tcp_dst"] - 5000))
        ctl = self.find(dp, table_id=0, priority=C.PRIO_CONTROL)[0]
        self.assertEqual(ctl.match["tcp_dst"], 5000)
        self.assertEqual(actions_of(ctl)[0].queue_id, C.Q_HIGH)
        self.assertEqual(goto_of(ctl), 1)
        self.assertEqual(len(ms), 1 + 2 + 3 + 1)

    def test_defaults_baseline_installs_only_priority_zero_rows(self):
        dp = FakeDP()
        flows.install_defaults(dp, "baseline")
        added = [m for m in mods(dp) if m.command == dp.ofproto.OFPFC_ADD]
        self.assertEqual({(m.table_id, m.priority) for m in added}, {(0, 0), (1, 0)})

    def test_allow_and_remove(self):
        dp = FakeDP()
        flows.allow(dp, "10.0.0.2")
        added = mods(dp)
        self.assertEqual(sorted(m.match["tcp_dst"] for m in added), [5001, 5002, 5003])
        for m in added:
            port = m.match["tcp_dst"]
            self.assertEqual((m.table_id, m.priority), (0, C.PRIO_ALLOW))
            self.assertEqual(m.match["ipv4_src"], "10.0.0.2")
            self.assertEqual(m.cookie, C.COOKIE_ALLOW + port - 5000)
            self.assertEqual(actions_of(m)[0].queue_id, C.QUEUE_BY_PORT[port])
            self.assertEqual(goto_of(m), 1)
        dp.sent.clear()
        flows.remove_allow(dp, "10.0.0.2")
        for m in mods(dp):
            port = m.match["tcp_dst"]
            self.assertEqual(m.command, dp.ofproto.OFPFC_DELETE_STRICT)
            self.assertEqual((m.table_id, m.priority), (0, C.PRIO_ALLOW))
            self.assertEqual(m.match["ipv4_src"], "10.0.0.2")
            self.assertEqual(m.cookie, C.COOKIE_ALLOW + port - 5000)
            self.assertEqual(m.cookie_mask, 0xFFFFFFFFFFFFFFFF)
        self.assertEqual(len(mods(dp)), 3)

    def test_the_allow_rule_outranks_the_drop_rule(self):
        self.assertGreater(C.PRIO_ALLOW, C.PRIO_DROP)
        self.assertGreater(C.PRIO_BLOCK, C.PRIO_ALLOW)

    def test_block_and_remove_cover_all_chat_ports(self):
        dp = FakeDP()
        flows.block(dp, "10.0.0.3")
        ms = mods(dp)
        self.assertEqual(sorted(m.match["tcp_dst"] for m in ms), [5000, 5001, 5002, 5003])
        for m in ms:
            self.assertEqual((m.table_id, m.priority, m.instructions), (0, C.PRIO_BLOCK, []))
            self.assertEqual(m.cookie, C.COOKIE_BLOCK + m.match["tcp_dst"] - 5000)
        dp.sent.clear()
        flows.remove_block(dp, "10.0.0.3")
        self.assertEqual({m.command for m in mods(dp)}, {dp.ofproto.OFPFC_DELETE_STRICT})

    def test_route_and_remove_route(self):
        dp = FakeDP()
        flows.route(dp, "00:00:00:00:00:02", 2)
        m = mods(dp)[0]
        self.assertEqual((m.table_id, m.priority), (1, C.PRIO_ROUTE))
        self.assertEqual(m.match["eth_dst"], "00:00:00:00:00:02")
        self.assertEqual(actions_of(m)[0].port, 2)
        dp.sent.clear()
        flows.remove_route(dp, "00:00:00:00:00:02")
        self.assertEqual(mods(dp)[0].command, dp.ofproto.OFPFC_DELETE_STRICT)

    def test_packet_out(self):
        dp = FakeDP()
        flows.packet_out(dp, 3, b"\x01" * 60, [1, 2])
        po = dp.sent[0]
        self.assertEqual([a.port for a in po.actions], [1, 2])
        self.assertEqual(po.in_port, 3)
        self.assertEqual(po.data, b"\x01" * 60)


if __name__ == "__main__":
    unittest.main()
