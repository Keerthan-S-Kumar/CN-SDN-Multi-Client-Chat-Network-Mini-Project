#!/usr/bin/env python3
"""OWNER: C. Mininet topology, built exactly as docs/interfaces.md section 1.

Run: sudo python3 topology/topo.py --topo single|triangle [--cli]
  - OVS switches (OpenFlow13), RemoteController at OF_IP:OF_PORT
  - hosts h1..h4 with IPs from common.constants and MACs HOST_MAC
  - explicit port numbers on every link
  - root-namespace interface 10.0.0.254/24 (MAC CONTROLLER_MGMT_MAC) linked to s1
  - triangle: TCLink bw=LINK_BW_MBPS on switch-switch and client links, then topology/qos.py on s1 port 1
    (the h1 link is a plain link: its 10 Mbps bottleneck is the OVS HTB queue, and a second tc
    qdisc from TCLink on the same interface would fight with it)
Default (no --cli): run pingall and exit with status 0 only if nothing was lost.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from mininet.cli import CLI                       # noqa: E402
from mininet.link import Link, TCLink            # noqa: E402
from mininet.log import info, setLogLevel, warn  # noqa: E402
from mininet.net import Mininet                  # noqa: E402
from mininet.node import Node, OVSSwitch, RemoteController  # noqa: E402

from common import constants as C                # noqa: E402

PREFIX = 24


def dpid_str(n):
    return "%016x" % n


def build(topo):
    # Node() below marks the shared base class as set up, which would skip OVSSwitch's own
    # lazy setup (it reads the OVS version), so do it explicitly first.
    OVSSwitch.setup()
    net = Mininet(controller=None, switch=OVSSwitch, link=Link,
                  autoSetMacs=False, autoStaticArp=False, waitConnected=False)
    net.addController("c0", controller=RemoteController, ip=C.OF_IP, port=C.OF_PORT)

    hosts = {}
    for name in ("h1", "h2", "h3", "h4"):
        ip = C.SERVER_IP if name == "h1" else C.CLIENT_IPS[name]
        hosts[name] = net.addHost(name, ip="%s/%d" % (ip, PREFIX), mac=C.HOST_MAC[name])

    root = Node("root", inNamespace=False)

    if topo == C.TOPO_SINGLE:
        s1 = net.addSwitch("s1", dpid=dpid_str(1), protocols="OpenFlow13")
        for name in ("h1", "h2", "h3", "h4"):
            net.addLink(hosts[name], s1, port1=0, port2=C.SINGLE_PORTS[name], cls=Link)
        root_link = net.addLink(root, s1, port2=C.SINGLE_PORTS["root"], cls=Link,
                                addr1=C.CONTROLLER_MGMT_MAC)
    else:
        sw = {n: net.addSwitch("s%d" % n, dpid=dpid_str(n), protocols="OpenFlow13")
              for n in (1, 2, 3)}
        for name, (dpid, port) in C.TRIANGLE_HOST_PORTS.items():
            if (dpid, port) == (C.BOTTLENECK_DPID, C.BOTTLENECK_PORT):
                net.addLink(hosts[name], sw[dpid], port1=0, port2=port, cls=Link)
            else:
                net.addLink(hosts[name], sw[dpid], port1=0, port2=port,
                            cls=TCLink, bw=C.LINK_BW_MBPS)
        for a, pa, b, pb in C.TRIANGLE_LINKS:
            net.addLink(sw[a], sw[b], port1=pa, port2=pb, cls=TCLink, bw=C.LINK_BW_MBPS)
        dpid, port = C.TRIANGLE_ROOT_PORT
        root_link = net.addLink(root, sw[dpid], port2=port, cls=Link,
                                addr1=C.CONTROLLER_MGMT_MAC)

    return net, root, root_link


def run(topo, cli):
    net, root, root_link = build(topo)
    try:
        net.start()
        root.setIP("%s/%d" % (C.CONTROLLER_MGMT_IP, PREFIX), intf=root_link.intf1)
        if not net.waitConnected(timeout=20):
            warn("*** switches did not connect to the controller at %s:%d - is ryu-manager running?\n"
                 % (C.OF_IP, C.OF_PORT))
        if topo == C.TOPO_TRIANGLE:
            from topology import qos
            info("*** QoS on %s\n" % qos.setup_qos())
        if cli:
            info("*** root-namespace interface: %s = %s\n" % (root_link.intf1, C.CONTROLLER_MGMT_IP))
            CLI(net)
            return 0
        info("*** warm-up round (first packets trigger ARP/flow learning, not counted)\n")
        net.pingAll(timeout=2)
        info("*** measured round\n")
        loss = net.pingAll(timeout=2)
        return 0 if loss == 0 else 1
    finally:
        net.stop()
        if topo == C.TOPO_TRIANGLE:
            try:
                from topology import qos
                qos.clear_qos()
            except Exception:
                pass


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--topo", choices=(C.TOPO_SINGLE, C.TOPO_TRIANGLE), default=C.TOPO_SINGLE)
    p.add_argument("--cli", action="store_true", help="open the Mininet CLI instead of pingall")
    args = p.parse_args()
    setLogLevel("info")
    return run(args.topo, args.cli)


if __name__ == "__main__":
    raise SystemExit(main())
