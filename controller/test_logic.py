"""OWNER: B. Controller tests that need no Mininet/OVS.
Run: PYTHONPATH=. python3 -m unittest controller.test_logic -v
Part 1 is pure python. Part 2 (marked needs_ryu) builds and *serializes* real OpenFlow 1.3 messages through
Ryu's own parser and drives the app with fake switches; it is skipped if Ryu is not importable.
"""
import itertools
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from common import constants as C
from controller import flows
from controller.monitor import Congestion, bps, heavy_hosts
from controller.policy import Policy
from controller.routing import Topo

# ---- optional Ryu (os-ken is accepted for local testing) -------------------
try:
    import ryu  # noqa: F401
    HAVE_RYU = True
except ImportError:
    try:
        import importlib
        import os_ken
        import types
        sys.modules["ryu"] = os_ken
        try:
            importlib.import_module("os_ken.app.wsgi")
            _wsgi_ok = True
        except ImportError:                      # newer os-ken dropped the REST helper: stub it (tests don't use REST)
            _wsgi_ok = False
            stub = types.ModuleType("ryu.app.wsgi")
            stub.ControllerBase = type("ControllerBase", (), {"__init__": lambda self, *a, **k: None})
            stub.WSGIApplication = type("WSGIApplication", (), {})
            stub.Response = lambda **k: SimpleNamespace(**k)
            stub.route = lambda *a, **k: (lambda f: f)
            sys.modules["ryu.app.wsgi"] = stub
        for sub in ("app", "base", "base.app_manager", "controller", "controller.ofp_event",
                    "controller.handler", "lib", "lib.hub", "lib.packet", "lib.packet.packet",
                    "lib.packet.ethernet", "lib.packet.arp", "ofproto", "ofproto.ofproto_v1_3",
                    "ofproto.ofproto_v1_3_parser"):
            sys.modules["ryu." + sub] = importlib.import_module("os_ken." + sub)
        _am = sys.modules["ryu.base.app_manager"]
        if not hasattr(_am, "RyuApp"):           # os-ken renamed the base class
            _am.RyuApp = _am.OSKenApp
        HAVE_RYU = True
    except ImportError:
        HAVE_RYU = False
needs_ryu = unittest.skipUnless(HAVE_RYU, "Ryu not installed")


class TestRouting(unittest.TestCase):
    def test_paths_and_failover(self):
        t = Topo()
        self.assertEqual(t.path(2, 3), [2, 3])
        self.assertEqual(t.path(2, 1), [2, 1])
        self.assertTrue(t.set_link(2, 3, up=False))
        self.assertFalse(t.set_link(2, 3, up=False))          # second end of same link: no change
        self.assertEqual(t.path(2, 3), [2, 1, 3])
        self.assertEqual(t.out_port(2, 3, 1), 2)              # s2 -> s1 port
        t.set_link(1, 2, up=False)
        self.assertIsNone(t.path(2, 1))
        self.assertIsNone(t.out_port(2, 1, 1))
        t.set_link(1, 2, up=True)
        self.assertEqual(t.path(2, 1), [2, 1])

    def test_ports_and_routes(self):
        t = Topo()
        self.assertEqual(t.inter_ports(3), {3, 4})
        self.assertEqual(t.link_of(1, 2), (1, 2))
        self.assertEqual(t.link_of(2, 2), (1, 2))
        self.assertIsNone(t.link_of(1, 1))
        self.assertEqual(t.routes_to(1, 1, [1, 2, 3]), {1: 1, 2: 2, 3: 3})
        self.assertEqual(Topo([]).routes_to(1, 1, [1]), {1: 1})   # single-switch topology


class TestMonitor(unittest.TestCase):
    def test_rate(self):
        self.assertEqual(bps(0, 125000, 1.0), 1_000_000)
        self.assertEqual(bps(10, 5, 1.0), 0.0)

    def test_congestion_hysteresis(self):
        c = Congestion(0.8, 0.5, 2)
        seq = [0.9, 0.9, 0.7, 0.4, 0.4]
        self.assertEqual([c.update(u) for u in seq], [None, "on", None, None, "off"])
        self.assertEqual(c.update(0.9), None)                    # needs 2 samples again

    def test_heavy(self):
        self.assertEqual(heavy_hosts({"a": 4e6, "b": 1e6}, 10e6, 0.3), {"a"})


class TestPolicy(unittest.TestCase):
    def test_policy(self):
        p = Policy()
        p.update({"vip_users": ["alice"], "vip_rooms": ["vip"], "banned_users": ["eve"]})
        self.assertTrue(p.is_vip("alice") and p.is_vip("bob", ["x", "vip"]) and not p.is_vip("bob", ["x"]))
        self.assertTrue(p.is_banned("eve"))
        with self.assertRaises(ValueError):
            p.update({"vip_users": "alice"})
        self.assertTrue(p.is_banned("eve"))                      # bad update leaves state untouched

    def test_default_file_loads(self):
        p = Policy(os.path.join(os.path.dirname(__file__), "policy.json"))
        self.assertIn("alice", p.data["vip_users"])


class TestRuleSpecs(unittest.TestCase):
    def test_families(self):
        ip = "10.0.0.2"
        allow = flows.rule_specs("allow", ip)
        self.assertEqual([s["cookie"] for s in allow], [0x5001, 0x5002, 0x5003])
        self.assertEqual([s["queue"] for s in allow], [C.Q_NORMAL, C.Q_HIGH, C.Q_BULK])
        self.assertEqual(len(flows.rule_specs("block", ip)), 4)
        self.assertEqual(flows.rule_specs("vip", ip)[0]["cookie"], 0x6001)
        self.assertEqual(flows.rule_specs("throttle", ip)[0]["meter"], C.METER_THROTTLE)
        self.assertEqual(flows.rule_specs("heavy", ip)[0]["meter"], C.METER_HEAVY)

    def test_no_collisions(self):
        seen = set()
        for cat in flows.CATEGORIES:
            for s in flows.rule_specs(cat, "10.0.0.2"):
                k = (s["prio"], tuple(sorted(s["match"].items())))
                self.assertNotIn(k, seen)
                seen.add(k)
        for s in flows.static_specs():
            self.assertNotIn((s["prio"], tuple(sorted(s["match"].items()))), seen)


# ---- Part 2: real OpenFlow messages + fake switches ------------------------
class FakeDP:
    def __init__(self, dpid):
        from ryu.ofproto import ofproto_v1_3, ofproto_v1_3_parser
        self.id, self.ofproto, self.ofproto_parser = dpid, ofproto_v1_3, ofproto_v1_3_parser
        self.sent, self._xid = [], itertools.count(1)

    def send_msg(self, msg):
        msg.xid = next(self._xid)
        msg.serialize()                                          # raises if a field/instruction is invalid
        self.sent.append(msg)

    def flowmods(self, command=None):
        out = [m for m in self.sent if isinstance(m, self.ofproto_parser.OFPFlowMod)]
        return [m for m in out if command is None or m.command == command]


MAC = C.HOST_MAC


@needs_ryu
class TestApp(unittest.TestCase):
    def setUp(self):
        from controller import app as appmod
        self.appmod = appmod
        self.tmp = tempfile.mkdtemp()
        self.patches = [mock.patch.object(appmod, "CONTROLLER_LOG", os.path.join(self.tmp, "log.jsonl")),
                        mock.patch.object(appmod.hub, "spawn", lambda *a, **k: None)]
        for p in self.patches:
            p.start()
        self.app = appmod.ChatSdn(wsgi=SimpleNamespace(register=lambda *a, **k: None))
        self.dps = {i: FakeDP(i) for i in (1, 2, 3)}
        self.app.dps = dict(self.dps)
        self.app.ports.update({1: {1, 2, 3, 4}, 2: {1, 2, 3}, 3: {1, 2, 3, 4}})
        self.of = self.dps[1].ofproto

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def ev(self, event, user, ip, room=None):
        body = {"event": event, "user": user, "ip": ip}
        if room:
            body["room"] = room
        return self.app.handle_event(body)

    def clear(self):
        for d in self.dps.values():
            d.sent.clear()

    def test_defaults_serialize(self):
        d = FakeDP(9)
        flows.install_defaults(d, sdn=True)
        flows.install_route(d, MAC["h1"], 1)
        flows.clear_routes(d)
        flows.packet_out(d, 2, b"x" * 20)
        for cat in flows.CATEGORIES:
            flows.apply_rules(d, cat, "10.0.0.2", add=True)
            flows.apply_rules(d, cat, "10.0.0.2", add=False)
        base = FakeDP(8)
        flows.install_defaults(base, sdn=False)
        self.assertEqual(len(base.flowmods(base.ofproto.OFPFC_ADD)), 2)          # baseline: prio-0 rows only
        # 2 prio-0 rows + 4 static + (allow 3, vip 1, throttle 3, block 4, heavy 1) + 1 route
        self.assertEqual(len(d.flowmods(d.ofproto.OFPFC_ADD)), 2 + 4 + (3 + 1 + 3 + 4 + 1) + 1)

    def test_register_installs_and_disconnect_removes(self):
        self.assertEqual(self.ev("register", "carol", "10.0.0.2")[0], 200)     # (not a VIP user)
        for d in self.dps.values():
            adds = [m for m in d.flowmods(self.of.OFPFC_ADD) if m.priority == C.PRIO_ALLOW]
            self.assertEqual(sorted(m.cookie for m in adds), [0x5001, 0x5002, 0x5003])
        self.assertEqual(self.app.state()["registered"], {"10.0.0.2": "carol"})
        self.clear()
        self.ev("register", "dave", "10.0.0.2")                   # same host, second user: nothing new
        self.assertEqual(self.dps[1].flowmods(), [])
        self.ev("disconnect", "carol", "10.0.0.2")                # dave still there: rules stay
        self.assertEqual(self.dps[1].flowmods(), [])
        self.ev("disconnect", "dave", "10.0.0.2")
        dels = self.dps[1].flowmods(self.of.OFPFC_DELETE_STRICT)
        self.assertEqual(sorted(m.cookie for m in dels), [0x5001, 0x5002, 0x5003])
        self.assertEqual(self.app.state()["registered"], {})

    def test_empty_ip_is_resolved_from_user(self):
        self.ev("register", "carol", "10.0.0.2")
        self.ev("flag", "carol", "10.0.0.2")
        self.assertEqual(self.app.metrics()["throttled"], ["10.0.0.2"])
        self.assertEqual(self.ev("unflag", "carol", "")[0], 200)              # spam_watch sends ip=""
        self.assertEqual(self.app.metrics()["throttled"], [])
        self.assertEqual(self.ev("unflag", "ghost", ""), (200, {"ok": True, "note": "unknown user, nothing to do"}))
        self.assertEqual(self.ev("register", "carol", "")[0], 400)           # register still needs an ip

    def test_bad_events(self):
        self.assertEqual(self.ev("bogus", "a", "10.0.0.2")[0], 400)
        self.assertEqual(self.ev("join", "a", "10.0.0.2")[0], 400)          # room missing
        self.assertEqual(self.app.handle_event({"event": "register", "user": "a"})[0], 400)

    def test_flag_block_vip(self):
        self.ev("register", "alice", "10.0.0.2")                   # alice is in policy.json vip_users
        self.assertIn("10.0.0.2", self.app.installed["vip"])
        self.ev("register", "bob", "10.0.0.3")
        self.assertNotIn("10.0.0.3", self.app.installed["vip"])
        self.ev("join", "bob", "10.0.0.3", "vip")                  # vip room membership
        self.assertIn("10.0.0.3", self.app.installed["vip"])
        self.ev("leave", "bob", "10.0.0.3", "vip")
        self.assertNotIn("10.0.0.3", self.app.installed["vip"])
        self.ev("flag", "bob", "10.0.0.3")
        self.assertEqual(self.app.metrics()["throttled"], ["10.0.0.3"])
        self.ev("unflag", "bob", "10.0.0.3")
        self.assertEqual(self.app.metrics()["throttled"], [])
        self.ev("block", "", "10.0.0.4")
        blocks = [m for m in self.dps[2].flowmods(self.of.OFPFC_ADD) if m.priority == C.PRIO_BLOCK]
        self.assertEqual(len(blocks), 4)
        self.assertEqual(self.app.state()["blocked"], ["10.0.0.4"])
        self.clear()
        self.app.expire_blocks(self.app.blocked["10.0.0.4"] + 1)   # monitor loop calls this every second
        self.assertEqual(self.app.state()["blocked"], [])
        dels = [m for m in self.dps[2].flowmods(self.of.OFPFC_DELETE_STRICT) if m.priority == C.PRIO_BLOCK]
        self.assertEqual(len(dels), 4)

    def test_baseline_installs_nothing(self):
        self.app.sdn, self.app.mode = False, "baseline"
        self.ev("register", "alice", "10.0.0.2")
        self.ev("flag", "alice", "10.0.0.2")
        self.assertTrue(all(d.flowmods() == [] for d in self.dps.values()))

    def test_link_failure_reroute(self):
        a = self.app
        a.hosts = {MAC["h1"]: (1, 1), MAC["h2"]: (2, 1)}
        a.reinstall_routes()
        r = {m.match["eth_dst"]: m for m in self.dps[2].flowmods(self.of.OFPFC_ADD)
             if m.priority == C.PRIO_ROUTE}
        self.assertEqual(r[MAC["h1"]].instructions[0].actions[0].port, 2)         # s2 -> s1 direct
        self.clear()
        a.on_link_change(1, 2, down=True)
        a.on_link_change(2, 2, down=True)                                          # other end: ignored
        self.assertEqual(a.metrics()["links_down"], [[1, 2]])
        r = {m.match["eth_dst"]: m for m in self.dps[2].flowmods(self.of.OFPFC_ADD)
             if m.priority == C.PRIO_ROUTE}
        self.assertEqual(r[MAC["h1"]].instructions[0].actions[0].port, 3)         # now via s3
        self.assertTrue(any(isinstance(m, self.dps[2].ofproto_parser.OFPBarrierRequest)
                            for m in self.dps[2].sent))
        a.on_link_change(1, 2, down=False)
        self.assertEqual(a.metrics()["links_down"], [])

    def test_baseline_link_failure_is_static(self):
        a = self.app
        a.sdn, a.mode = False, "baseline"
        a.hosts = {MAC["h1"]: (1, 1), MAC["h2"]: (2, 1)}
        a.on_link_change(1, 2, down=True)
        self.assertEqual(a.metrics()["links_down"], [])
        self.assertTrue(all(d.flowmods() == [] for d in self.dps.values()))

    def test_packet_in_learns_and_floods_edges_only(self):
        from ryu.lib.packet import arp, ethernet, packet
        pkt = packet.Packet()
        pkt.add_protocol(ethernet.ethernet(dst="ff:ff:ff:ff:ff:ff", src=MAC["h2"], ethertype=0x0806))
        pkt.add_protocol(arp.arp(src_mac=MAC["h2"], src_ip="10.0.0.2", dst_mac="00:00:00:00:00:00",
                                 dst_ip="10.0.0.1"))
        pkt.serialize()
        d2 = self.dps[2]
        msg = SimpleNamespace(datapath=d2, match={"in_port": 1}, data=bytes(pkt.data))
        self.app.on_packet_in(SimpleNamespace(msg=msg))
        self.assertEqual(self.app.hosts[MAC["h2"]], (2, 1))
        outs = {(d.id, m.actions[0].port) for d in self.dps.values() for m in d.sent
                if isinstance(m, d.ofproto_parser.OFPPacketOut)}
        # edge ports: s1 {1,4}, s2 {1} (ingress 1 excluded), s3 {1,2}; never inter-switch ports
        self.assertEqual(outs, {(1, 1), (1, 4), (3, 1), (3, 2)})

    def test_congestion_heavy_hitter_and_recovery(self):
        a, clk = self.app, {"t": 100.0}
        a.hosts = {MAC["h3"]: (3, 1)}

        def stats(dpid, port, rx, tx):
            body = [SimpleNamespace(port_no=port, rx_bytes=rx, tx_bytes=tx)]
            a.on_port_stats(SimpleNamespace(msg=SimpleNamespace(datapath=self.dps[dpid], body=body)))

        # h3's edge port receives 12 Mbit/s; bottleneck s1:1 transmits 9.5 Mbit/s (util 0.95)
        with mock.patch.object(self.appmod.time, "time", side_effect=lambda: clk["t"]):
            for k in range(4):
                clk["t"] += 1.0
                stats(3, 1, k * 1_500_000, 0)
                stats(1, C.BOTTLENECK_PORT, 0, k * 1_187_500)
        self.assertTrue(a.cong.active)
        self.assertEqual(a.metrics()["heavy"], ["10.0.0.3"])
        heavy = [m for m in self.dps[3].flowmods(self.of.OFPFC_ADD) if m.priority == C.PRIO_HEAVY]
        self.assertEqual(len(heavy), 1)
        with mock.patch.object(self.appmod.time, "time", side_effect=lambda: clk["t"]):
            base_rx, base_tx = 3 * 1_500_000, 3 * 1_187_500
            for _ in range(3):                                     # traffic stops
                clk["t"] += 1.0
                stats(3, 1, base_rx, 0)
                stats(1, C.BOTTLENECK_PORT, 0, base_tx)
        self.assertFalse(a.cong.active)
        self.assertEqual(a.metrics()["heavy"], [])
        kinds = [__import__("json").loads(l)["kind"] for l in open(self.appmod.CONTROLLER_LOG)]
        self.assertIn("congestion_on", kinds)
        self.assertIn("heavy_on", kinds)
        self.assertIn("congestion_off", kinds)
        self.assertIn("heavy_off", kinds)

    def test_explain_gives_reasons(self):
        self.ev("register", "bob", "10.0.0.3")
        self.ev("flag", "bob", "10.0.0.3")
        self.ev("block", "", "10.0.0.4")
        ex = self.app.explain()
        by = {(a["category"], a["ip"]): a for a in ex["actions"]}
        self.assertIn("spam", by[("throttle", "10.0.0.3")]["reason"])
        self.assertEqual(by[("throttle", "10.0.0.3")]["user"], "bob")
        self.assertIn("brute force", by[("block", "10.0.0.4")]["reason"])
        self.assertGreater(by[("block", "10.0.0.4")]["expires_in_s"], 0)
        self.assertEqual({r["cat"] for r in ex["rules"]}, {"gate", "allow", "vip", "throttle", "block", "heavy"})
        self.ev("unflag", "bob", "10.0.0.3")
        self.assertNotIn(("throttle", "10.0.0.3"), {(a["category"], a["ip"]) for a in self.app.explain()["actions"]})

    def test_traffic_counts_bytes_per_class_at_the_edge_switch(self):
        def stat(ip, port, n, prio=C.PRIO_ALLOW):
            return SimpleNamespace(table_id=C.TABLE_POLICY, priority=prio, byte_count=n,
                                   match=SimpleNamespace(items=lambda: [("ipv4_src", ip), ("tcp_dst", port)]))
        self.app.hosts[MAC["h2"]] = (2, 1)
        self.app._count_traffic(2, [stat("10.0.0.2", C.PORT_ROOM, 1000), stat("10.0.0.2", C.PORT_BULK, 5000)])
        self.app._count_traffic(1, [stat("10.0.0.2", C.PORT_ROOM, 999999)])           # transit switch: ignored
        self.app._count_traffic(2, [stat("10.0.0.2", C.PORT_ROOM, 1500), stat("10.0.0.2", C.PORT_BULK, 9000)])
        t = self.app.traffic()
        self.assertEqual(t["hosts"]["10.0.0.2"]["bytes"]["room"], 1500)
        self.assertEqual(t["hosts"]["10.0.0.2"]["bytes"]["bulk"], 9000)
        self.assertEqual(t["classes"]["bulk"], 9000)
        self.app._count_traffic(2, [stat("10.0.0.2", C.PORT_ROOM, 100)])             # counter reset after re-install
        self.assertEqual(self.app.traffic()["hosts"]["10.0.0.2"]["bytes"]["room"], 1600)

    def test_admin_actions(self):
        self.ev("register", "bob", "10.0.0.3")
        self.ev("flag", "bob", "10.0.0.3")
        self.assertEqual(self.app.admin({"action": "unflag", "ip": "10.0.0.3"})[0], 200)
        self.assertEqual(self.app.metrics()["throttled"], [])
        self.app.admin({"action": "block", "ip": "10.0.0.3"})
        self.assertEqual(self.app.state()["blocked"], ["10.0.0.3"])
        self.app.admin({"action": "unblock", "ip": "10.0.0.3"})
        self.assertEqual(self.app.state()["blocked"], [])
        self.app.admin({"action": "vip", "user": "bob"})
        self.assertIn("10.0.0.3", self.app.installed["vip"])
        self.app.admin({"action": "ban", "user": "bob"})
        self.assertEqual(self.app.state()["blocked"], ["10.0.0.3"])
        self.assertEqual(self.app.admin({"action": "nope"})[0], 400)

    def test_barrier_logs_install_time(self):
        self.ev("register", "alice", "10.0.0.2")
        reqs = [(d.id, m) for d in self.dps.values() for m in d.sent
                if isinstance(m, d.ofproto_parser.OFPBarrierRequest)]
        self.assertEqual(len(reqs), 3)
        for dpid, m in reqs:
            self.app.on_barrier(SimpleNamespace(msg=SimpleNamespace(datapath=self.dps[dpid], xid=m.xid)))
        self.assertGreaterEqual(self.app.metrics()["last_flow_install_ms"], 0.0)
        self.assertIn("flow_install", open(self.appmod.CONTROLLER_LOG).read())


if __name__ == "__main__":
    unittest.main()
