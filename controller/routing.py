"""OWNER: B. Table-1 routing per docs/interfaces.md §7.
Pure-python graph part (BFS over TRIANGLE_LINKS minus down links) must be testable without Ryu:
  next_hops(src_dpid, dst_dpid, down_links) -> [(dpid, out_port), ...]
State is keyed by dpid. D1 'single' topology = one switch, no links."""
# TODO (B)
