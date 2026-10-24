"""OWNER: C. OVS QoS on the bottleneck port (s1 port 1, toward h1) per docs/interfaces.md section 9.

linux-htb, BOTTLENECK_BPS max, queues 0-3 taken from common.constants.QUEUE_PARAMS, configured with
ovs-vsctl. Called by topo.py for --topo triangle. clear_qos() removes everything again.
Needs root. If meters turn out unsupported, tell B early (fallback: ingress policing).
"""
import subprocess

from common import constants as C


def port_name():
    """Mininet names a switch interface s<dpid>-eth<port> when ports are explicit."""
    return "s%d-eth%d" % (C.BOTTLENECK_DPID, C.BOTTLENECK_PORT)


def _ovs(args):
    subprocess.check_call(["ovs-vsctl"] + list(args))


def clear_qos(port=None):
    """Detach QoS from the port and delete all QoS/Queue records (safe to call repeatedly)."""
    port = port or port_name()
    subprocess.call(["ovs-vsctl", "--if-exists", "clear", "port", port, "qos"],
                    stderr=subprocess.DEVNULL)
    subprocess.call(["ovs-vsctl", "--all", "destroy", "qos"], stderr=subprocess.DEVNULL)
    subprocess.call(["ovs-vsctl", "--all", "destroy", "queue"], stderr=subprocess.DEVNULL)


def setup_qos(port=None):
    """Create the HTB QoS record with queues 0..3 and attach it to the bottleneck port."""
    port = port or port_name()
    clear_qos(port)
    args = ["set", "port", port, "qos=@newqos", "--",
            "--id=@newqos", "create", "qos", "type=linux-htb",
            "other-config:max-rate=%d" % C.BOTTLENECK_BPS]
    for q in sorted(C.QUEUE_PARAMS):
        args.append("queues:%d=@q%d" % (q, q))
    for q in sorted(C.QUEUE_PARAMS):
        mn, mx, prio = C.QUEUE_PARAMS[q]
        args += ["--", "--id=@q%d" % q, "create", "queue",
                 "other-config:min-rate=%d" % mn,
                 "other-config:max-rate=%d" % mx,
                 "other-config:priority=%d" % prio]
    _ovs(args)
    return port


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "clear":
        clear_qos()
        print("QoS cleared on", port_name())
    else:
        print("QoS set on", setup_qos())
