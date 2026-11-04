"""OWNER: B. Ryu app entry point (OpenFlow 1.3).

Run: CONTROLLER_MODE=sdn|baseline PYTHONPATH=. ryu-manager controller/app.py
Implements docs/interfaces.md §4 (events/REST), §5 (flow table), §7 (routing), §10 (dynamic behaviour).

Design: state (sessions, rooms, flags, blocks, heavy hitters) is the source of truth; `reconcile()` diffs the
rule families it implies against what is installed and pushes only the difference to every switch, then
measures install time with a barrier. A reconnecting switch is re-synced from the same state.
`baseline` mode keeps the prio-0 rows and static shortest-path routes only: no gating, queues, meters,
monitoring, or reaction to link events.
"""
import json
import os
import time
from collections import defaultdict

from ryu.app.wsgi import WSGIApplication
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, DEAD_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.lib import hub
from ryu.lib.packet import ethernet, packet
from ryu.ofproto import ofproto_v1_3

from common.constants import (
    BLOCK_SECONDS, BOTTLENECK_BPS, BOTTLENECK_DPID, BOTTLENECK_PORT, CLIENT_IPS, CONTROLLER_LOG, EVENTS,
    HOST_MAC, MODE, MONITOR_INTERVAL, PRIO_DROP, TABLE_POLICY, TRIANGLE_LINKS)
from controller import flows
from controller.monitor import Congestion, bps, heavy_hosts
from controller.policy import Policy
from controller.rest_api import APP_KEY, Api
from controller.routing import Topo

CONTROLLER_LOG = os.environ.get("CHATSDN_LOG", CONTROLLER_LOG)     # the experiment runner sets one file per run
POLICY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "policy.json")
MAC_TO_IP = {HOST_MAC[h]: ip for h, ip in CLIENT_IPS.items()}
ETH_LLDP = 0x88CC


class ChatSdn(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {"wsgi": WSGIApplication}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mode = MODE if MODE in ("sdn", "baseline") else "sdn"
        self.sdn = self.mode == "sdn"
        self.dps = {}                              # dpid -> datapath
        self.ports = defaultdict(set)              # dpid -> port numbers
        self.hosts = {}                            # mac -> (dpid, port)
        self.topo = Topo(TRIANGLE_LINKS)
        self.triangle = False                      # sticky: set once s2/s3 have connected
        self.base_down = set()                     # baseline mode only: links seen down (for the log)
        self.policy = Policy(POLICY_FILE)
        # chat state (from server events)
        self.sessions = defaultdict(set)           # ip -> users logged in from that ip
        self.user_ip = {}
        self.rooms = defaultdict(set)              # room -> users
        self.flagged = defaultdict(set)            # ip -> flagged users
        self.blocked = {}                          # ip -> expiry (unix time)
        self.heavy = set()
        self.installed = {c: set() for c in flows.CATEGORIES}
        # monitoring
        self.cong = Congestion()
        self.pstat = {}                            # (dpid, port) -> (t, rx_bytes, tx_bytes)
        self.rx_rate, self.tx_rate = {}, {}
        self.drop_hits_by_dp = {}
        self.util = 0.0
        self.barriers = {}                         # (dpid, xid) -> pending dict
        self.last_flow_install_ms = 0.0
        self.last_reroute_ms = 0.0
        os.makedirs(os.path.dirname(os.path.abspath(CONTROLLER_LOG)), exist_ok=True)
        kwargs["wsgi"].register(Api, {APP_KEY: self})
        try:                                       # dashboard is optional: it must never break the event API
            from controller.ui_api import UiApi
            kwargs["wsgi"].register(UiApi, {APP_KEY: self})
        except Exception as e:                     # noqa: BLE001
            self.logger.warning("dashboard disabled: %s", e)
        if self.sdn:
            hub.spawn(self._monitor_loop)
        self.logger.info("ChatSdn started in %s mode", self.mode)

    # ---- logging / metrics ------------------------------------------------
    def log(self, kind, **detail):
        rec = {"t": time.time(), "kind": kind, "detail": detail}
        try:
            with open(CONTROLLER_LOG, "a") as f:
                f.write(json.dumps(rec) + "\n")
        except OSError:
            pass

    def state(self):
        return {"registered": {ip: self.last_user(ip) for ip, u in self.sessions.items() if u},
                "rooms": {r: sorted(m) for r, m in self.rooms.items() if m},
                "flagged": sorted(ip for ip, u in self.flagged.items() if u),
                "blocked": sorted(self.blocked)}

    def metrics(self):
        return {"mode": self.mode, "congested": self.cong.active, "util": round(self.util, 4),
                "throttled": sorted(self.installed["throttle"]), "heavy": sorted(self.heavy),
                "blocked": sorted(self.blocked),
                "links_down": sorted(sorted(k) for k in self.topo.down),
                "drop_hits": sum(self.drop_hits_by_dp.values()),
                "last_flow_install_ms": round(self.last_flow_install_ms, 3),
                "last_reroute_ms": round(self.last_reroute_ms, 3)}

    def snapshot(self):
        """Everything the dashboard needs that /state and /metrics do not already say."""
        return {"mode": self.mode,
                "topology": "triangle" if self.is_triangle() else "single",
                "switches": sorted(self.dps),
                "rules": {c: sorted(ips) for c, ips in self.installed.items()},
                "hosts": {mac: list(loc) for mac, loc in self.hosts.items()},
                "bottleneck_bps": BOTTLENECK_BPS,
                "links": [sorted((a, b)) for a, _, b, _ in TRIANGLE_LINKS]}

    def last_user(self, ip):
        return sorted(self.sessions[ip])[-1] if self.sessions.get(ip) else None

    # ---- topology helpers -------------------------------------------------
    def is_triangle(self):
        return self.triangle or any(d in self.dps for d in (2, 3))

    def edge_ports(self, dpid):
        ports = self.ports[dpid]
        return ports - self.topo.inter_ports(dpid) if self.is_triangle() else set(ports)

    # ---- switch lifecycle -------------------------------------------------
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def on_features(self, ev):
        dp = ev.msg.datapath
        self.dps[dp.id] = dp
        self.triangle = self.triangle or dp.id in (2, 3)
        flows.install_defaults(dp, self.sdn)
        dp.send_msg(dp.ofproto_parser.OFPPortDescStatsRequest(dp, 0))
        self.sync_dp(dp)
        self.logger.info("switch %s connected", dp.id)

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def on_state(self, ev):
        dp = ev.datapath
        if ev.state == DEAD_DISPATCHER and self.dps.get(dp.id) is dp:
            del self.dps[dp.id]
            self.logger.info("switch %s disconnected", dp.id)

    def sync_dp(self, dp):
        """Bring a (re)connected switch in line with current state."""
        if self.sdn:
            for cat, ips in self.installed.items():
                for ip in ips:
                    flows.apply_rules(dp, cat, ip, add=True)
        for mac in self.hosts:
            self.install_host_route(mac, only_dp=dp)

    @set_ev_cls(ofp_event.EventOFPPortDescStatsReply, MAIN_DISPATCHER)
    def on_port_desc(self, ev):
        dp, ofp = ev.msg.datapath, ev.msg.datapath.ofproto
        for p in ev.msg.body:
            if p.port_no < ofp.OFPP_MAX:
                self.ports[dp.id].add(p.port_no)

    @set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)
    def on_port_status(self, ev):
        msg, dp = ev.msg, ev.msg.datapath
        ofp, port = dp.ofproto, ev.msg.desc.port_no
        if port >= ofp.OFPP_MAX:
            return
        if msg.reason == ofp.OFPPR_DELETE:
            self.ports[dp.id].discard(port)
            down = True
        else:
            self.ports[dp.id].add(port)
            down = bool(msg.desc.state & ofp.OFPPS_LINK_DOWN) or bool(msg.desc.config & ofp.OFPPC_PORT_DOWN)
        self.on_link_change(dp.id, port, down)

    def on_link_change(self, dpid, port, down):
        link = self.topo.link_of(dpid, port) if self.is_triangle() else None
        if not link:
            return
        t0 = time.time()
        if not self.sdn:                   # baseline is static: log it, never react, never touch the graph
            if (link in self.base_down) == down:
                return
            self.base_down.add(link) if down else self.base_down.discard(link)
            self.log("link_down" if down else "link_up", link=list(link))
            return
        if not self.topo.set_link(link[0], link[1], up=not down):
            return                         # second end of the same link
        self.log("link_down" if down else "link_up", link=list(link))
        self.reinstall_routes()
        self.barrier(t0, "reroute" if down else "link_up")

    # ---- routing (table 1) ------------------------------------------------
    def install_host_route(self, mac, only_dp=None):
        loc = self.hosts.get(mac)
        if not loc:
            return
        targets = {only_dp.id: only_dp} if only_dp else self.dps
        routes = self.topo.routes_to(loc[0], loc[1], list(targets))
        for dpid, port in routes.items():
            flows.install_route(targets[dpid], mac, port)

    def reinstall_routes(self):
        for dp in self.dps.values():
            flows.clear_routes(dp)
        for mac in self.hosts:
            self.install_host_route(mac)

    def flood_edges(self, in_dpid, in_port, data):
        for dpid, dp in self.dps.items():
            for p in self.edge_ports(dpid):
                if not (dpid == in_dpid and p == in_port):
                    flows.packet_out(dp, p, data)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def on_packet_in(self, ev):
        msg, dp = ev.msg, ev.msg.datapath
        in_port = msg.match["in_port"]
        eth = packet.Packet(msg.data).get_protocol(ethernet.ethernet)
        if eth is None or eth.ethertype == ETH_LLDP:
            return
        if in_port in self.edge_ports(dp.id) and self.hosts.get(eth.src) != (dp.id, in_port):
            moved = eth.src in self.hosts
            self.hosts[eth.src] = (dp.id, in_port)
            self.install_host_route(eth.src)
            if moved:
                self.log("host_moved", mac=eth.src, at=[dp.id, in_port])
        loc = self.hosts.get(eth.dst)
        is_multicast = int(eth.dst.split(":")[0], 16) & 1
        if is_multicast or loc is None:
            self.flood_edges(dp.id, in_port, msg.data)
            return
        out = self.topo.out_port(dp.id, loc[0], loc[1])
        if out is not None:
            flows.packet_out(dp, out, msg.data, in_port=in_port)

    # ---- barrier timing ---------------------------------------------------
    def barrier(self, t0, kind):
        """Send a barrier to every switch; log how long until all have confirmed the preceding FlowMods."""
        if not self.dps:
            return
        pend = {"n": len(self.dps), "t0": t0, "kind": kind}
        for dp in list(self.dps.values()):
            req = dp.ofproto_parser.OFPBarrierRequest(dp)
            dp.send_msg(req)
            self.barriers[(dp.id, req.xid)] = pend

    @set_ev_cls(ofp_event.EventOFPBarrierReply, MAIN_DISPATCHER)
    def on_barrier(self, ev):
        pend = self.barriers.pop((ev.msg.datapath.id, ev.msg.xid), None)
        if not pend:
            return
        pend["n"] -= 1
        if pend["n"] > 0:
            return
        ms = (time.time() - pend["t0"]) * 1000.0
        if pend["kind"] == "reroute":
            self.last_reroute_ms = ms
            self.log("reroute_done", ms=ms)
        else:
            self.last_flow_install_ms = ms
            self.log("flow_install", trigger=pend["kind"], ms=ms)

    # ---- chat events (REST) ----------------------------------------------
    def handle_event(self, e):
        t0 = time.time()
        ev, ip, user = e.get("event"), e.get("ip"), e.get("user", "")
        if ev not in EVENTS:
            return 400, {"ok": False, "error": "unknown event %r" % (ev,)}
        if not isinstance(ip, str) or not isinstance(user, str):
            return 400, {"ok": False, "error": "ip and user must be strings"}
        if ev != "block" and not user:
            return 400, {"ok": False, "error": "user required"}
        if not ip and ev in ("unflag", "flag", "leave", "disconnect"):
            ip = self.user_ip.get(user, "")        # server sends ip="" for a user who already left
            if not ip:
                return 200, {"ok": True, "note": "unknown user, nothing to do"}
        if not ip:
            return 400, {"ok": False, "error": "ip required"}
        room = e.get("room")
        if ev in ("join", "leave") and not (isinstance(room, str) and room):
            return 400, {"ok": False, "error": "room required"}
        self.log("event_rx", event=ev, user=user, ip=ip, room=room)

        if ev in ("register", "login"):
            self.sessions[ip].add(user)
            self.user_ip[user] = ip
            if self.policy.is_banned(user):
                self.block_ip(ip)
        elif ev == "disconnect":
            self.sessions[ip].discard(user)
            if not self.sessions[ip]:
                del self.sessions[ip]
            self.user_ip.pop(user, None)
            self.flagged[ip].discard(user)
            for members in self.rooms.values():
                members.discard(user)
        elif ev == "join":
            self.rooms[room].add(user)
        elif ev == "leave":
            self.rooms[room].discard(user)
        elif ev == "flag":
            self.flagged[ip].add(user)
        elif ev == "unflag":
            self.flagged[ip].discard(user)
        elif ev == "block":
            self.block_ip(ip)
        if self.reconcile():
            self.barrier(t0, ev)
        return 200, {"ok": True}

    def block_ip(self, ip):
        self.blocked[ip] = time.time() + BLOCK_SECONDS

    def set_policy(self, d):
        self.policy.update(d)
        for ip, users in list(self.sessions.items()):
            if any(self.policy.is_banned(u) for u in users):
                self.block_ip(ip)
        if self.reconcile():
            self.barrier(time.time(), "policy")

    # ---- desired state -> rules ------------------------------------------
    def _rooms_of(self, user):
        return [r for r, m in self.rooms.items() if user in m]

    def desired(self):
        if not self.sdn:
            return {c: set() for c in flows.CATEGORIES}
        return {
            "allow": {ip for ip, u in self.sessions.items() if u},
            "vip": {ip for ip, users in self.sessions.items()
                    if any(self.policy.is_vip(u, self._rooms_of(u)) for u in users)},
            "throttle": {ip for ip, u in self.flagged.items() if u},
            "block": set(self.blocked),
            "heavy": set(self.heavy),
        }

    def reconcile(self):
        """Push the difference between desired and installed rules to all switches. True if anything changed."""
        want, changed = self.desired(), False
        for cat in flows.CATEGORIES:
            add, rem = want[cat] - self.installed[cat], self.installed[cat] - want[cat]
            for dp in self.dps.values():
                for ip in add:
                    flows.apply_rules(dp, cat, ip, add=True)
                for ip in rem:
                    flows.apply_rules(dp, cat, ip, add=False)
            self.installed[cat] = set(want[cat])
            for ip in add:
                self._log_category(cat, ip, True)
            for ip in rem:
                self._log_category(cat, ip, False)
            changed = changed or bool(add or rem)
        return changed

    def _log_category(self, cat, ip, on):
        kind = {"throttle": "throttle", "block": "block", "heavy": "heavy"}.get(cat)
        if kind:
            self.log("%s_%s" % (kind, "on" if on else "off"), ip=ip)

    # ---- monitoring (sdn mode) -------------------------------------------
    def _monitor_loop(self):
        while True:
            hub.sleep(MONITOR_INTERVAL)
            self.expire_blocks(time.time())
            for dp in list(self.dps.values()):
                p, ofp = dp.ofproto_parser, dp.ofproto
                dp.send_msg(p.OFPPortStatsRequest(dp, 0, ofp.OFPP_ANY))
                dp.send_msg(p.OFPFlowStatsRequest(dp, 0, TABLE_POLICY))

    def expire_blocks(self, now):
        expired = [ip for ip, t in self.blocked.items() if t <= now]
        for ip in expired:
            del self.blocked[ip]
        if expired and self.reconcile():
            self.barrier(now, "block_expiry")

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def on_port_stats(self, ev):
        dpid, now = ev.msg.datapath.id, time.time()
        for s in ev.msg.body:
            key, prev = (dpid, s.port_no), self.pstat.get((dpid, s.port_no))
            if prev:
                dt = now - prev[0]
                self.rx_rate[key] = bps(prev[1], s.rx_bytes, dt)
                self.tx_rate[key] = bps(prev[2], s.tx_bytes, dt)
            self.pstat[key] = (now, s.rx_bytes, s.tx_bytes)
        if dpid != BOTTLENECK_DPID or (dpid, BOTTLENECK_PORT) not in self.tx_rate:
            return
        self.util = self.tx_rate[(dpid, BOTTLENECK_PORT)] / float(BOTTLENECK_BPS)
        verdict = self.cong.update(self.util)
        if verdict == "on":
            self.log("congestion_on", util=self.util)
        elif verdict == "off":
            self.log("congestion_off", util=self.util)
            self.heavy.clear()
        if self.cong.active:
            self.heavy |= heavy_hosts(self._host_rates(), BOTTLENECK_BPS)
        if (verdict or self.cong.active) and self.reconcile():
            self.barrier(now, "congestion")

    def _host_rates(self):
        """{ip: rx bits/s on the host's edge port} for client hosts whose location is known."""
        out = {}
        for mac, ip in MAC_TO_IP.items():
            loc = self.hosts.get(mac)
            if loc and loc in self.rx_rate:
                out[ip] = self.rx_rate[loc]
        return out

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def on_flow_stats(self, ev):
        self.drop_hits_by_dp[ev.msg.datapath.id] = sum(
            s.packet_count for s in ev.msg.body if s.priority == PRIO_DROP and s.table_id == TABLE_POLICY)
