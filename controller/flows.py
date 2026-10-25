"""OWNER: B. FlowMod / MeterMod builders — one rule family per row of docs/interfaces.md §5.

`rule_specs()` is pure data (testable without Ryu). The builders use only `dp.ofproto` and
`dp.ofproto_parser`, so this module imports nothing from Ryu.
Rules are deleted with DELETE_STRICT on exact priority + match + cookie — never a loose delete.
"""
from common.constants import (
    BLOCK_PORTS, COOKIE_ALLOW, COOKIE_BLOCK, COOKIE_HEAVY, COOKIE_THROTTLE, COOKIE_VIP,
    GATED_PORTS, HEAVY_KBPS, METER_HEAVY, METER_THROTTLE, PORT_CONTROL, PORT_ROOM, PRIO_ALLOW,
    PRIO_BLOCK, PRIO_CONTROL, PRIO_DEFAULT, PRIO_DROP, PRIO_HEAVY, PRIO_ROUTE, PRIO_THROTTLE,
    PRIO_VIP, Q_BULK, Q_HIGH, Q_OTHER, QUEUE_BY_PORT, TABLE_POLICY, TABLE_ROUTE, THROTTLE_KBPS)

COOKIE_ROUTE = 0x1000            # table-1 route flows (implementation detail, see §5/§7)
ALL = 0xFFFFFFFFFFFFFFFF
CATEGORIES = ("allow", "vip", "throttle", "block", "heavy")


def _tcp(port, ip=None):
    m = {"eth_type": 0x0800, "ip_proto": 6, "tcp_dst": port}
    if ip is not None:
        m["ipv4_src"] = ip
    return m


def _spec(prio, match, cookie, queue=None, meter=None, drop=False):
    return {"prio": prio, "match": match, "cookie": cookie, "queue": queue, "meter": meter, "drop": drop}


def static_specs():
    """Rules every sdn-mode switch gets at connect: gate drops (prio 100) + control classification (prio 150)."""
    out = [_spec(PRIO_DROP, _tcp(p), COOKIE_ALLOW + (p - PORT_CONTROL), drop=True) for p in GATED_PORTS]
    out.append(_spec(PRIO_CONTROL, _tcp(PORT_CONTROL), COOKIE_ALLOW, queue=Q_HIGH))
    return out


def rule_specs(category, ip):
    """Per-IP rule family -> list of spec dicts (see docs/interfaces.md §5)."""
    if category == "allow":
        return [_spec(PRIO_ALLOW, _tcp(p, ip), COOKIE_ALLOW + (p - PORT_CONTROL), queue=QUEUE_BY_PORT[p])
                for p in GATED_PORTS]
    if category == "vip":
        return [_spec(PRIO_VIP, _tcp(PORT_ROOM, ip), COOKIE_VIP + (PORT_ROOM - PORT_CONTROL), queue=Q_HIGH)]
    if category == "throttle":
        return [_spec(PRIO_THROTTLE, _tcp(p, ip), COOKIE_THROTTLE + (p - PORT_CONTROL),
                      queue=Q_BULK, meter=METER_THROTTLE) for p in GATED_PORTS]
    if category == "block":
        return [_spec(PRIO_BLOCK, _tcp(p, ip), COOKIE_BLOCK + (p - PORT_CONTROL), drop=True)
                for p in BLOCK_PORTS]
    if category == "heavy":
        return [_spec(PRIO_HEAVY, {"eth_type": 0x0800, "ipv4_src": ip}, COOKIE_HEAVY,
                      queue=Q_OTHER, meter=METER_HEAVY)]
    raise ValueError(category)


# ---- message builders -----------------------------------------------------

def _instructions(dp, spec):
    ofp, p = dp.ofproto, dp.ofproto_parser
    if spec["drop"]:
        return []
    ins = []
    if spec["meter"] is not None:
        ins.append(p.OFPInstructionMeter(meter_id=spec["meter"]))
    if spec["queue"] is not None:
        ins.append(p.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, [p.OFPActionSetQueue(spec["queue"])]))
    ins.append(p.OFPInstructionGotoTable(TABLE_ROUTE))
    return ins


def _add(dp, table, prio, match, ins, cookie=0):
    ofp, p = dp.ofproto, dp.ofproto_parser
    dp.send_msg(p.OFPFlowMod(
        datapath=dp, cookie=cookie, cookie_mask=0, table_id=table, command=ofp.OFPFC_ADD,
        priority=prio, buffer_id=ofp.OFP_NO_BUFFER, out_port=ofp.OFPP_ANY, out_group=ofp.OFPG_ANY,
        match=p.OFPMatch(**match), instructions=ins))


def _delete_strict(dp, table, prio, match, cookie):
    ofp, p = dp.ofproto, dp.ofproto_parser
    dp.send_msg(p.OFPFlowMod(
        datapath=dp, cookie=cookie, cookie_mask=ALL, table_id=table, command=ofp.OFPFC_DELETE_STRICT,
        priority=prio, buffer_id=ofp.OFP_NO_BUFFER, out_port=ofp.OFPP_ANY, out_group=ofp.OFPG_ANY,
        match=p.OFPMatch(**match), instructions=[]))


def apply_rules(dp, category, ip, add=True):
    for s in rule_specs(category, ip):
        if add:
            _add(dp, TABLE_POLICY, s["prio"], s["match"], _instructions(dp, s), s["cookie"])
        else:
            _delete_strict(dp, TABLE_POLICY, s["prio"], s["match"], s["cookie"])


def install_defaults(dp, sdn):
    """Wipe the switch, then install the prio-0 rows (both modes) and, in sdn mode, the static rows + meters."""
    ofp, p = dp.ofproto, dp.ofproto_parser
    dp.send_msg(p.OFPFlowMod(datapath=dp, table_id=ofp.OFPTT_ALL, command=ofp.OFPFC_DELETE,
                             out_port=ofp.OFPP_ANY, out_group=ofp.OFPG_ANY, match=p.OFPMatch()))
    dp.send_msg(p.OFPMeterMod(datapath=dp, command=ofp.OFPMC_DELETE, flags=0,
                              meter_id=ofp.OFPM_ALL, bands=[]))
    _add(dp, TABLE_POLICY, PRIO_DEFAULT, {}, [p.OFPInstructionGotoTable(TABLE_ROUTE)])
    _add(dp, TABLE_ROUTE, PRIO_DEFAULT, {},
         [p.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS,
                                  [p.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)])])
    if not sdn:
        return
    for mid, kbps in ((METER_THROTTLE, THROTTLE_KBPS), (METER_HEAVY, HEAVY_KBPS)):
        dp.send_msg(p.OFPMeterMod(
            datapath=dp, command=ofp.OFPMC_ADD, flags=ofp.OFPMF_KBPS, meter_id=mid,
            bands=[p.OFPMeterBandDrop(rate=kbps, burst_size=max(10, kbps // 10))]))
    for s in static_specs():
        _add(dp, TABLE_POLICY, s["prio"], s["match"], _instructions(dp, s), s["cookie"])


def install_route(dp, mac, out_port):
    """Table 1, prio 10: eth_dst -> output."""
    p, ofp = dp.ofproto_parser, dp.ofproto
    _add(dp, TABLE_ROUTE, PRIO_ROUTE, {"eth_dst": mac},
         [p.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, [p.OFPActionOutput(out_port)])], COOKIE_ROUTE)


def clear_routes(dp):
    """Delete every table-1 route flow (matched by cookie, so the table-miss rule survives)."""
    ofp, p = dp.ofproto, dp.ofproto_parser
    dp.send_msg(p.OFPFlowMod(
        datapath=dp, cookie=COOKIE_ROUTE, cookie_mask=ALL, table_id=TABLE_ROUTE, command=ofp.OFPFC_DELETE,
        out_port=ofp.OFPP_ANY, out_group=ofp.OFPG_ANY, match=p.OFPMatch()))


def packet_out(dp, out_port, data, in_port=None):
    ofp, p = dp.ofproto, dp.ofproto_parser
    dp.send_msg(p.OFPPacketOut(
        datapath=dp, buffer_id=ofp.OFP_NO_BUFFER, in_port=in_port if in_port is not None else ofp.OFPP_CONTROLLER,
        actions=[p.OFPActionOutput(out_port)], data=data))
