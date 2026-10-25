"""OWNER: B. FlowMod builders, one function per row of docs/interfaces.md section 5.

Every function takes a Ryu datapath `dp` and sends the message itself. All numbers come from
common.constants. Deletes always use an exact cookie + priority + match (strict), never a loose delete.
"""
from common import constants as C

ETH_IP = 0x0800
IP_TCP = 6
ALL_COOKIES = 0xFFFFFFFFFFFFFFFF


def _tcp_match(parser, ip=None, port=None):
    fields = {"eth_type": ETH_IP, "ip_proto": IP_TCP}
    if ip is not None:
        fields["ipv4_src"] = ip
    if port is not None:
        fields["tcp_dst"] = port
    return parser.OFPMatch(**fields)


def _apply(dp, actions):
    return dp.ofproto_parser.OFPInstructionActions(dp.ofproto.OFPIT_APPLY_ACTIONS, actions)


def _goto(dp, table):
    return dp.ofproto_parser.OFPInstructionGotoTable(table)


def _add(dp, table, priority, match, instructions, cookie=0):
    ofp, parser = dp.ofproto, dp.ofproto_parser
    dp.send_msg(parser.OFPFlowMod(
        datapath=dp, cookie=cookie, cookie_mask=0, table_id=table, command=ofp.OFPFC_ADD,
        idle_timeout=0, hard_timeout=0, priority=priority, buffer_id=ofp.OFP_NO_BUFFER,
        out_port=ofp.OFPP_ANY, out_group=ofp.OFPG_ANY, flags=0, match=match, instructions=instructions))


def _delete_strict(dp, table, priority, match, cookie):
    ofp, parser = dp.ofproto, dp.ofproto_parser
    dp.send_msg(parser.OFPFlowMod(
        datapath=dp, cookie=cookie, cookie_mask=ALL_COOKIES, table_id=table,
        command=ofp.OFPFC_DELETE_STRICT, priority=priority, out_port=ofp.OFPP_ANY,
        out_group=ofp.OFPG_ANY, match=match))


def clear_all(dp):
    """Start from an empty flow table (used when a switch connects)."""
    ofp, parser = dp.ofproto, dp.ofproto_parser
    dp.send_msg(parser.OFPFlowMod(
        datapath=dp, table_id=ofp.OFPTT_ALL, command=ofp.OFPFC_DELETE,
        out_port=ofp.OFPP_ANY, out_group=ofp.OFPG_ANY, match=parser.OFPMatch()))


def install_defaults(dp, mode):
    """Prio-0 rows (both modes) and, in sdn mode, the static policy rows: gating drop + control queue."""
    ofp, parser = dp.ofproto, dp.ofproto_parser
    clear_all(dp)
    _add(dp, C.TABLE_POLICY, C.PRIO_DEFAULT, parser.OFPMatch(), [_goto(dp, C.TABLE_ROUTE)])
    to_controller = parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)
    _add(dp, C.TABLE_ROUTE, C.PRIO_DEFAULT, parser.OFPMatch(), [_apply(dp, [to_controller])])
    if mode != "sdn":
        return
    for port in C.GATED_PORTS:                                        # prio 100: unregistered hosts are dropped
        _add(dp, C.TABLE_POLICY, C.PRIO_DROP, _tcp_match(parser, port=port), [],
             cookie=C.COOKIE_ALLOW + (port - C.PORT_CONTROL))
    _add(dp, C.TABLE_POLICY, C.PRIO_CONTROL, _tcp_match(parser, port=C.PORT_CONTROL),   # prio 150: port 5000 open
         [_apply(dp, [parser.OFPActionSetQueue(C.Q_HIGH)]), _goto(dp, C.TABLE_ROUTE)],
         cookie=C.COOKIE_ALLOW)


def allow(dp, ip):
    """Prio 200: a registered IP may use 5001-5003, each port in its own queue."""
    parser = dp.ofproto_parser
    for port in C.GATED_PORTS:
        _add(dp, C.TABLE_POLICY, C.PRIO_ALLOW, _tcp_match(parser, ip, port),
             [_apply(dp, [parser.OFPActionSetQueue(C.QUEUE_BY_PORT[port])]), _goto(dp, C.TABLE_ROUTE)],
             cookie=C.COOKIE_ALLOW + (port - C.PORT_CONTROL))


def remove_allow(dp, ip):
    parser = dp.ofproto_parser
    for port in C.GATED_PORTS:
        _delete_strict(dp, C.TABLE_POLICY, C.PRIO_ALLOW, _tcp_match(parser, ip, port),
                       C.COOKIE_ALLOW + (port - C.PORT_CONTROL))


def block(dp, ip):
    """Prio 400: drop all chat traffic (5000-5003) from a blocked IP."""
    parser = dp.ofproto_parser
    for port in C.BLOCK_PORTS:
        _add(dp, C.TABLE_POLICY, C.PRIO_BLOCK, _tcp_match(parser, ip, port), [],
             cookie=C.COOKIE_BLOCK + (port - C.PORT_CONTROL))


def remove_block(dp, ip):
    parser = dp.ofproto_parser
    for port in C.BLOCK_PORTS:
        _delete_strict(dp, C.TABLE_POLICY, C.PRIO_BLOCK, _tcp_match(parser, ip, port),
                       C.COOKIE_BLOCK + (port - C.PORT_CONTROL))


def route(dp, eth_dst, out_port):
    """Table 1, prio 10: send frames for `eth_dst` out of `out_port`."""
    parser = dp.ofproto_parser
    _add(dp, C.TABLE_ROUTE, C.PRIO_ROUTE, parser.OFPMatch(eth_dst=eth_dst),
         [_apply(dp, [parser.OFPActionOutput(out_port)])])


def remove_route(dp, eth_dst):
    _delete_strict(dp, C.TABLE_ROUTE, C.PRIO_ROUTE, dp.ofproto_parser.OFPMatch(eth_dst=eth_dst), 0)


def packet_out(dp, in_port, data, out_ports):
    ofp, parser = dp.ofproto, dp.ofproto_parser
    actions = [parser.OFPActionOutput(p) for p in out_ports]
    dp.send_msg(parser.OFPPacketOut(datapath=dp, buffer_id=ofp.OFP_NO_BUFFER, in_port=in_port,
                                    actions=actions, data=data))
