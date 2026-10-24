"""OWNER: C. OVS QoS on s1 port 1 per docs/interfaces.md §9 (linux-htb, 10 Mbps, queues 0-3 from
common.constants.QUEUE_PARAMS) via ovs-vsctl. Called by topo.py for --topo triangle.
Provide clear_qos() too. If meters turn out unsupported, tell B early (fallback: ingress policing)."""
# TODO (C)
