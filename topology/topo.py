"""OWNER: C. Mininet topology.

Run: sudo python3 topology/topo.py --topo single|triangle [--cli]
Build exactly docs/interfaces.md §1:
  - OVS switches with protocols=OpenFlow13, RemoteController at OF_IP:OF_PORT
  - hosts h1..h4 with IPs from common.constants and MACs HOST_MAC
  - EXPLICIT port numbers (addLink(..., port1=, port2=)) per SINGLE_PORTS / TRIANGLE_* / TRIANGLE_LINKS
  - root-namespace interface 10.0.0.254/24, MAC CONTROLLER_MGMT_MAC, linked to s1 (port per §1)
  - triangle: TCLink bw=LINK_BW_MBPS everywhere, then topology/qos.py on s1 port 1
Default (no --cli): run pingall and exit.
"""

if __name__ == "__main__":
    raise SystemExit("TODO (C): implement topology")
