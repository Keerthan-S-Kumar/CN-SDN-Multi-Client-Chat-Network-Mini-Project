"""OWNER: B. Ryu app entry point (OpenFlow 1.3).

Run: CONTROLLER_MODE=sdn|baseline PYTHONPATH=. ryu-manager controller/app.py
Wires together flows.py, routing.py, policy.py, monitor.py, rest_api.py.
Implements docs/interfaces.md §4 (events/REST), §5 (flow table), §7 (routing), §10 (dynamic behaviour).
Import every number from common.constants — never hard-code ports, priorities, cookies, queues.
D1 scope: single topology, allow/drop rules, L2 routing, /event /health /state. Keep state keyed by dpid.
"""
# TODO (B)
