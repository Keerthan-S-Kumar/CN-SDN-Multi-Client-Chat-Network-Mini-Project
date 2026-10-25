"""OWNER: B. Ryu app entry point (OpenFlow 1.3).

Run: CONTROLLER_MODE=sdn|baseline PYTHONPATH=. ryu-manager controller/app.py
D1 scope: single topology, L2 forwarding, gating (drop 5001-5003 for unregistered hosts), allow on
register/login, remove on disconnect, REST /event /health /state /metrics /policy.
The same code already routes the D2 triangle (loop-free shortest path, edge-port-only flooding).
Not done yet (D2): flag/unflag throttling (needs meters), congestion monitor, link-failure rerouting.

State: self.datapaths {dpid: dp}, self.hosts {mac: (dpid, port)}, self.registered {ip: user},
self.sessions {ip: set(users)} (an IP stays allowed while any user on it is connected).
"""
import ipaddress
import json
import logging
import os
import time

from ryu.app.wsgi import WSGIApplication
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, DEAD_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.lib import hub
from ryu.lib.packet import ethernet, ether_types, packet
from ryu.ofproto import ofproto_v1_3

from common import constants as C
from controller import flows, routing
from controller.policy import Policy
from controller.rest_api import ChatRest

BARRIER_TIMEOUT = 1.0


class ChatController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {"wsgi": WSGIApplication}

    def __init__(self, *args, **kwargs):
        super(ChatController, self).__init__(*args, **kwargs)
        self.mode = C.MODE if C.MODE in C.MODES else "sdn"
        self.topo_override = os.environ.get("TOPO")          # "single" | "triangle"; default: detect
        self.datapaths = {}
        self.hosts = {}
        self.registered = {}
        self.sessions = {}
        self.rooms = {}
        self.flagged = []
        self.blocked = []
        self.down_links = set()
        self.drop_hits = 0
        self.last_flow_install_ms = 0.0
        self.last_reroute_ms = 0.0
        self.policy = Policy()
        self._barriers = {}
        kwargs["wsgi"].register(ChatRest, {"app": self})
        self.logger.info("chat controller started, mode=%s", self.mode)

    # ---- helpers ---------------------------------------------------------------
    def kind(self):
        return self.topo_override or routing.topology_kind(self.datapaths)

    def log_event(self, kind, detail=None):
        """Append one JSON line to eval/results/controller_events.jsonl (never raises)."""
        try:
            os.makedirs(os.path.dirname(C.CONTROLLER_LOG), exist_ok=True)
            with open(C.CONTROLLER_LOG, "a") as f:
                f.write(json.dumps({"t": time.time(), "kind": kind, "detail": detail or {}}) + "\n")
        except OSError:
            pass

    def barrier(self, dp):
        """Block this green thread until the switch has processed everything sent before (or timeout)."""
        req = dp.ofproto_parser.OFPBarrierRequest(dp)
        xid = dp.set_xid(req)
        ev = hub.Event()
        self._barriers[(dp.id, xid)] = ev
        dp.send_msg(req)
        ok = ev.wait(timeout=BARRIER_TIMEOUT)
        self._barriers.pop((dp.id, xid), None)
        return ok

    def barrier_all(self):
        for dp in list(self.datapaths.values()):
            self.barrier(dp)

    # ---- switch lifecycle -----------------------------------------------------
    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change(self, ev):
        dp = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[dp.id] = dp
        elif ev.state == DEAD_DISPATCHER and self.datapaths.get(dp.id) is dp:
            del self.datapaths[dp.id]

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def _switch_features(self, ev):
        dp = ev.msg.datapath
        flows.install_defaults(dp, self.mode)
        if self.mode == "sdn":
            for ip in self.registered:                      # switch reconnected: restore known users
                flows.allow(dp, ip)
            for ip in self.blocked:
                flows.block(dp, ip)
        self.logger.info("switch %s connected, defaults installed (%s)", dp.id, self.mode)

    @set_ev_cls(ofp_event.EventOFPBarrierReply, MAIN_DISPATCHER)
    def _barrier_reply(self, ev):
        waiter = self._barriers.get((ev.msg.datapath.id, ev.msg.xid))
        if waiter is not None:
            waiter.set()

    # ---- forwarding (table 1) ---------------------------------------------------
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in(self, ev):
        msg = ev.msg
        dp = msg.datapath
        in_port = msg.match["in_port"]
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]
        if eth.ethertype in (ether_types.ETH_TYPE_LLDP, ether_types.ETH_TYPE_IPV6):
            return
        kind = self.kind()
        edge = routing.edge_ports(kind, dp.id)
        if in_port in edge:
            self.hosts[eth.src] = (dp.id, in_port)           # learn only on edge ports
        dst = self.hosts.get(eth.dst)
        multicast = int(eth.dst.split(":")[0], 16) & 1
        if dst is None or multicast:
            self._flood(dp.id, in_port, msg.data, kind)
            return
        dst_dpid, dst_port = dst
        hops = routing.next_hops(dp.id, dst_dpid, self.down_links, kind)
        if hops is None:
            return                                           # destination unreachable: drop
        path = hops + [(dst_dpid, dst_port)]
        for dpid, out_port in path:
            if dpid in self.datapaths:
                flows.route(self.datapaths[dpid], eth.dst, out_port)
        first_dpid, first_port = path[0]
        if first_dpid == dp.id and not (first_port == in_port):
            flows.packet_out(dp, in_port, msg.data, [first_port])

    def _flood(self, in_dpid, in_port, data, kind):
        """Unknown / broadcast destination: out of every edge port of every switch, except where it came in."""
        for dpid, dp in list(self.datapaths.items()):
            ports = set(routing.edge_ports(kind, dpid))
            if dpid == in_dpid:
                ports.discard(in_port)
            if ports:
                flows.packet_out(dp, dp.ofproto.OFPP_CONTROLLER, data, sorted(ports))

    # ---- events from the chat server (POST /event) ---------------------------------
    def handle_event(self, e):
        """Returns (http_status, json_body). Never raises."""
        try:
            return self._handle_event(e)
        except Exception as ex:                              # a bad event must not kill the controller
            self.logger.exception("event failed: %r", e)
            return 500, {"ok": False, "error": str(ex)}

    def _handle_event(self, e):
        if not isinstance(e, dict):
            return 400, {"ok": False, "error": "event must be a JSON object"}
        ev, user, ip = e.get("event"), e.get("user"), e.get("ip")
        if ev not in C.EVENTS:
            return 400, {"ok": False, "error": "unknown event %r" % (ev,)}
        if not isinstance(user, str):
            return 400, {"ok": False, "error": "user must be a string"}
        try:
            if not isinstance(ip, str):
                raise ValueError("not a string")
            ip = str(ipaddress.IPv4Address(ip))
        except ValueError:
            return 400, {"ok": False, "error": "ip must be an IPv4 address"}
        room = e.get("room")
        if ev in ("join", "leave") and not isinstance(room, str):
            return 400, {"ok": False, "error": "%s needs a room" % ev}
        t0 = time.time()
        self.log_event("event_rx", {"event": ev, "user": user, "ip": ip})
        changed = False

        if ev in ("register", "login"):
            self.sessions.setdefault(ip, set()).add(user)
            self.registered[ip] = user
            if self.mode == "sdn":
                if self.policy.is_banned(user):
                    self._block(ip)
                else:
                    for dp in list(self.datapaths.values()):
                        flows.allow(dp, ip)
                changed = True
        elif ev == "disconnect":
            users = self.sessions.get(ip, set())
            users.discard(user)
            for members in self.rooms.values():
                members.discard(user)
            if not users:
                self.sessions.pop(ip, None)
                self.registered.pop(ip, None)
                if self.mode == "sdn":
                    for dp in list(self.datapaths.values()):
                        flows.remove_allow(dp, ip)
                    changed = True
            else:
                self.registered[ip] = sorted(users)[-1]
        elif ev == "join":
            self.rooms.setdefault(room, set()).add(user)
        elif ev == "leave":
            self.rooms.get(room, set()).discard(user)
        elif ev == "flag":                                   # D2: throttling needs meters, not built yet
            if ip not in self.flagged:
                self.flagged.append(ip)
        elif ev == "unflag":
            if ip in self.flagged:
                self.flagged.remove(ip)
        elif ev == "block":
            if self.mode == "sdn":
                self._block(ip)
                changed = True

        if changed:
            self.barrier_all()                               # reply only once the switches have the rules
            self.last_flow_install_ms = (time.time() - t0) * 1000.0
            self.log_event("flow_install", {"trigger": ev, "ms": self.last_flow_install_ms})
        return 200, {"ok": True}

    def _block(self, ip):
        if ip not in self.blocked:
            self.blocked.append(ip)
        for dp in list(self.datapaths.values()):
            flows.block(dp, ip)
        self.log_event("block_on", {"ip": ip})
        hub.spawn(self._unblock_later, ip, C.BLOCK_SECONDS)

    def _unblock_later(self, ip, seconds):
        hub.sleep(seconds)
        if ip in self.blocked:
            self.blocked.remove(ip)
        for dp in list(self.datapaths.values()):
            flows.remove_block(dp, ip)
        self.log_event("block_off", {"ip": ip})

    # ---- views for REST ------------------------------------------------------------
    def state_view(self):
        return {"registered": dict(self.registered),
                "rooms": {r: sorted(m) for r, m in self.rooms.items() if m},
                "flagged": list(self.flagged), "blocked": list(self.blocked)}

    def metrics_view(self):
        return {"mode": self.mode, "congested": False, "util": 0.0, "throttled": list(self.flagged),
                "heavy": [], "blocked": list(self.blocked), "links_down": [list(l) for l in self.down_links],
                "drop_hits": self.drop_hits, "last_flow_install_ms": self.last_flow_install_ms,
                "last_reroute_ms": self.last_reroute_ms}
