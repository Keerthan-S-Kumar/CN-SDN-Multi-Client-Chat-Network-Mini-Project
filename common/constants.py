"""Shared constants. FROZEN — see docs/interfaces.md. Stdlib only."""
import os

# ---- ports / classes -------------------------------------------------------
PORT_CONTROL, PORT_ROOM, PORT_PM, PORT_BULK = 5000, 5001, 5002, 5003
CHAT_PORTS = (PORT_CONTROL, PORT_ROOM, PORT_PM, PORT_BULK)
GATED_PORTS = (PORT_ROOM, PORT_PM, PORT_BULK)   # dropped for unregistered IPs
BLOCK_PORTS = CHAT_PORTS                        # dropped for blocked IPs

# ---- addressing ------------------------------------------------------------
SERVER_IP = "10.0.0.1"
CLIENT_IPS = {"h2": "10.0.0.2", "h3": "10.0.0.3", "h4": "10.0.0.4"}
HOST_MAC = {"h1": "00:00:00:00:00:01", "h2": "00:00:00:00:00:02",
            "h3": "00:00:00:00:00:03", "h4": "00:00:00:00:00:04"}
CONTROLLER_MGMT_IP = "10.0.0.254"
CONTROLLER_MGMT_MAC = "00:00:00:00:00:fe"
CONTROLLER_REST_PORT = 8080
CONTROLLER_URL = os.environ.get(
    "CONTROLLER_URL", "http://%s:%d" % (CONTROLLER_MGMT_IP, CONTROLLER_REST_PORT))
OF_IP, OF_PORT = "127.0.0.1", 6653

# ---- topology --------------------------------------------------------------
TOPO_SINGLE, TOPO_TRIANGLE = "single", "triangle"
# (dpid_a, port_a, dpid_b, port_b)
TRIANGLE_LINKS = [(1, 2, 2, 2), (1, 3, 3, 3), (2, 3, 3, 4)]
SINGLE_PORTS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "root": 5}               # on s1
TRIANGLE_HOST_PORTS = {"h1": (1, 1), "h2": (2, 1), "h3": (3, 1), "h4": (3, 2)}  # (dpid, port)
TRIANGLE_ROOT_PORT = (1, 4)
LINK_BW_MBPS = 100
BOTTLENECK_DPID, BOTTLENECK_PORT, BOTTLENECK_BPS = 1, 1, 10_000_000

# ---- events ----------------------------------------------------------------
EVENTS = ("register", "login", "disconnect", "join", "leave",
          "flag", "unflag", "block")

# ---- flow table ------------------------------------------------------------
MODE = os.environ.get("CONTROLLER_MODE", "sdn")      # "sdn" | "baseline"
TABLE_POLICY, TABLE_ROUTE = 0, 1
PRIO_DEFAULT, PRIO_ROUTE, PRIO_HEAVY, PRIO_DROP, PRIO_CONTROL, PRIO_ALLOW, \
    PRIO_VIP, PRIO_THROTTLE, PRIO_BLOCK = 0, 10, 60, 100, 150, 200, 250, 300, 400
COOKIE_ALLOW, COOKIE_VIP, COOKIE_THROTTLE, COOKIE_BLOCK, COOKIE_HEAVY = \
    0x5000, 0x6000, 0x7000, 0x8000, 0x9000           # cookie = base + (port - 5000)

Q_OTHER, Q_HIGH, Q_NORMAL, Q_BULK = 0, 1, 2, 3
QUEUE_BY_PORT = {PORT_CONTROL: Q_HIGH, PORT_ROOM: Q_NORMAL, PORT_PM: Q_HIGH, PORT_BULK: Q_BULK}
# (min_bps, max_bps, priority) for linux-htb on the bottleneck port
QUEUE_PARAMS = {Q_OTHER: (1_000_000, 10_000_000, 5), Q_HIGH: (3_000_000, 10_000_000, 1),
                Q_NORMAL: (3_000_000, 10_000_000, 2), Q_BULK: (500_000, 3_000_000, 6)}
METER_THROTTLE, METER_HEAVY = 1, 2
THROTTLE_KBPS, HEAVY_KBPS = 100, 2000

# ---- dynamic behaviour -----------------------------------------------------
MONITOR_INTERVAL = 1.0
CONGEST_ON, CONGEST_OFF, CONGEST_SAMPLES, HEAVY_SHARE = 0.8, 0.5, 2, 0.3
BLOCK_SECONDS = 60
SPAM_MSGS, SPAM_WINDOW, SPAM_QUIET = 20, 5.0, 30.0
MAX_BAD_LOGINS = 5

# ---- protocol --------------------------------------------------------------
MAX_FRAME = 64 * 1024
MAX_BULK = 60000

# ---- eval ------------------------------------------------------------------
RESULTS_DIR = "eval/results"
CONTROLLER_LOG = RESULTS_DIR + "/controller_events.jsonl"
MODES = ("sdn", "baseline")
SCENARIOS = ("idle", "congested", "flood", "linkfail",
             "scale5", "scale10", "scale25", "scale50", "scale100")
