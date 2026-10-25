"""OWNER: B. Table-1 routing per docs/interfaces.md section 7.

Pure python (no Ryu), so it can be unit-tested anywhere:
  topology_kind(dpids)                 'single' or 'triangle'
  edge_ports(kind, dpid)               ports that face hosts / the controller's root interface
  next_hops(src, dst, down_links)      [(dpid, out_port), ...] along the shortest path, or None
State is keyed by dpid. D1 'single' topology = one switch, no links.
"""
from collections import deque

from common import constants as C


def topology_kind(dpids):
    """The single topology only ever has switch 1; any of switches 2/3 means the triangle."""
    return C.TOPO_TRIANGLE if set(dpids) & {2, 3} else C.TOPO_SINGLE


def edge_ports(kind, dpid):
    """Ports of `dpid` that are not inter-switch links. Floods go out of these only (loop free)."""
    if kind == C.TOPO_SINGLE:
        return set(C.SINGLE_PORTS.values()) if dpid == 1 else set()
    ports = {p for d, p in C.TRIANGLE_HOST_PORTS.values() if d == dpid}
    if dpid == C.TRIANGLE_ROOT_PORT[0]:
        ports.add(C.TRIANGLE_ROOT_PORT[1])
    return ports


def _link_table(kind):
    """{(a, b): port on a toward b} for every switch-to-switch link."""
    table = {}
    if kind == C.TOPO_TRIANGLE:
        for a, pa, b, pb in C.TRIANGLE_LINKS:
            table[(a, b)] = pa
            table[(b, a)] = pb
    return table


def next_hops(src_dpid, dst_dpid, down_links=(), kind=C.TOPO_TRIANGLE):
    """Shortest switch path src -> dst as [(dpid, out_port)] for every switch EXCEPT the last one.

    Ties go to the lower DPID. `down_links` holds pairs like (1, 2) in either order.
    Returns [] when src == dst and None when dst cannot be reached.
    """
    if src_dpid == dst_dpid:
        return []
    down = {frozenset(p) for p in down_links}
    ports = _link_table(kind)
    neighbours = {}
    for (a, b) in ports:
        if frozenset((a, b)) not in down:
            neighbours.setdefault(a, []).append(b)
    prev = {src_dpid: None}
    queue = deque([src_dpid])
    while queue:
        cur = queue.popleft()
        if cur == dst_dpid:
            break
        for nxt in sorted(neighbours.get(cur, [])):
            if nxt not in prev:
                prev[nxt] = cur
                queue.append(nxt)
    if dst_dpid not in prev:
        return None
    path = [dst_dpid]
    while prev[path[-1]] is not None:
        path.append(prev[path[-1]])
    path.reverse()
    return [(a, ports[(a, b)]) for a, b in zip(path, path[1:])]
