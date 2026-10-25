"""OWNER: B. Table-1 routing graph (docs/interfaces.md §7). Pure python, no Ryu import.

The graph comes from common.constants.TRIANGLE_LINKS minus links marked down.
BFS tie-break: neighbours visited in ascending DPID order, so paths are deterministic.
"""
from collections import deque

from common.constants import TRIANGLE_LINKS


class Topo:
    def __init__(self, links=TRIANGLE_LINKS):
        self.links = list(links)
        self.down = set()                 # frozenset({dpid_a, dpid_b})
        self._link_at = {}                # (dpid, port) -> frozenset({a, b})
        self.adj = {}                     # dpid -> {neighbour dpid: local port}
        for a, pa, b, pb in self.links:
            self.adj.setdefault(a, {})[b] = pa
            self.adj.setdefault(b, {})[a] = pb
            key = frozenset((a, b))
            self._link_at[(a, pa)] = key
            self._link_at[(b, pb)] = key

    def inter_ports(self, dpid):
        """Ports of `dpid` that face another switch (all others are edge ports)."""
        return {p for (d, p) in self._link_at if d == dpid}

    def link_of(self, dpid, port):
        """(a, b) sorted dpids of the link on this port, or None for edge ports."""
        key = self._link_at.get((dpid, port))
        return tuple(sorted(key)) if key else None

    def set_link(self, a, b, up):
        """Mark a link up/down. Returns True if its state changed."""
        key = frozenset((a, b))
        was_up = key not in self.down
        if up and not was_up:
            self.down.discard(key)
            return True
        if not up and was_up:
            self.down.add(key)
            return True
        return False

    def _neighbours(self, dpid):
        for nbr in sorted(self.adj.get(dpid, {})):
            if frozenset((dpid, nbr)) not in self.down:
                yield nbr

    def path(self, src, dst):
        """Shortest list of dpids src..dst, or None if unreachable."""
        if src == dst:
            return [src]
        prev = {src: None}
        q = deque([src])
        while q:
            cur = q.popleft()
            for nbr in self._neighbours(cur):
                if nbr in prev:
                    continue
                prev[nbr] = cur
                if nbr == dst:
                    out = [dst]
                    while prev[out[-1]] is not None:
                        out.append(prev[out[-1]])
                    return out[::-1]
                q.append(nbr)
        return None

    def out_port(self, src, dst_dpid, dst_port):
        """Output port on switch `src` toward a host at (dst_dpid, dst_port); None if unreachable."""
        if src == dst_dpid:
            return dst_port
        p = self.path(src, dst_dpid)
        if not p:
            return None
        return self.adj[src][p[1]]

    def routes_to(self, dst_dpid, dst_port, dpids):
        """{dpid: out_port} on every switch in `dpids` that can reach the host."""
        out = {}
        for d in dpids:
            port = self.out_port(d, dst_dpid, dst_port)
            if port is not None:
                out[d] = port
        return out
