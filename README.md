# Multi-Client Chat Network over SDN (Project 8)

A multi-client chat application built on TCP sockets and run on a Mininet network controlled by a Ryu
SDN controller. The controller classifies chat traffic, applies user and room policies, and reacts to
congestion, spam, unauthorised access, brute-force logins and link failure. It is evaluated against a
static baseline controller.

Course: Computer Networks (UE25CS243A), SDN-Socket Programming mini project.

| Doc | What it covers |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | Components, topologies, flow-table design, dynamic behaviour |
| [`docs/interfaces.md`](docs/interfaces.md) | The frozen contract: ports, message formats, events, flow rows |
| [`docs/eval-plan.md`](docs/eval-plan.md) | Scenarios, metrics and result formats |
| [`docs/part-a.md`](docs/part-a.md) | Part A: chat server, client, bots, spam detection, analysis scripts |
| [`docs/ui.md`](docs/ui.md) | Live SDN dashboard (`/ui`) and browser chat client (`make web-chat`) |

## Team

| Person | Owns |
|---|---|
| A (Janhvi) | `chat/` (server, client, bots, spam detection), `eval/analyze.py`, `eval/plot.py` |
| B (Keerthan) | `controller/` (Ryu app: flows, routing, policy, monitor, REST) |
| C (Harsha) | `topology/`, `tests/`, `eval/run_experiments.py`, this README |

Shared and frozen: `common/`, `docs/`, `chat/protocol.py`, `chat/notifier.py`, `tools/`. Change them only
through a pull request approved by all three.

## Repository layout

```
chat/        TCP chat server, client, load-generating bots, shared wire protocol
controller/  Ryu OpenFlow 1.3 application and REST API
topology/    Mininet topologies (single, triangle) and OVS QoS queues
common/      constants shared by everyone (ports, IPs, priorities, queues, thresholds)
tests/       automated tests
eval/        experiment runner, analysis and plotting
tools/       mock controller, fake event sender, repo helpers
docs/        contract, architecture, evaluation plan
```

## Setup

Tested on an Ubuntu VM (VirtualBox) with Python 3.9

### 1. Mininet and Open vSwitch

```bash
sudo apt update
sudo apt install -y mininet openvswitch-switch git gh curl python3-pip python3-venv
sudo systemctl enable --now openvswitch-switch
sudo mn --test pingall          # expect: 0% dropped
```

### 2. Ryu (needs an older Python)

Ryu 4.34 does not build on current Python versions, so it runs in its own environment on Python 3.9.
It talks to Mininet only over OpenFlow, so the two Python versions do not need to match.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env
uv venv --python 3.9 ~/ryu-env
source ~/ryu-env/bin/activate
uv pip install "setuptools<58" wheel
uv pip install msgpack tinyrpc ovs
uv pip install --no-build-isolation --no-deps ryu==4.34
ryu-manager --version           # expect: ryu-manager 4.34
```

Install the Ryu dependencies first and use `--no-deps` for Ryu itself. A plain `pip install ryu` makes
the resolver fall back to a very old Ryu 2.x.

## Running

Always use this order, in separate terminals. **`sudo mn -c` kills `ryu-manager`, so run it first and
never while the controller is running.**

```bash
# Terminal 1: clean up, then start the controller
sudo mn -c
source ~/ryu-env/bin/activate
make controller MODE=sdn          # or MODE=baseline

# Terminal 2: topology (single for D1, triangle for D2)
make topo TOPO=single             # opens the Mininet CLI

# Terminal 3 (or inside Mininet: h1 python3 chat/server.py ...)
make server                       # chat server on h1 (10.0.0.1)
```

The controller listens on OpenFlow `127.0.0.1:6653` and REST `0.0.0.0:8080`. Hosts reach the REST API at
`http://10.0.0.254:8080`. For a quick network check without B's controller, use any forwarding controller:

```bash
ryu-manager ryu.app.simple_switch_13
sudo env PYTHONPATH=. python3 topology/topo.py --topo single      # pingall, exits 0 if nothing is lost
```

Useful inspection targets: `make flows`, `make meters`, `make queues`, `make metrics`.

## Tests

Topology tests need root and (for the single topology) a running OpenFlow 1.3 controller.

```bash
sudo mn -c                                                    # first, then start a controller
sudo env PYTHONPATH=. python3 -m unittest tests.test_topology -v   # single topology, 5 tests
sudo env PYTHONPATH=. python3 -m unittest tests.test_triangle -v   # triangle ports and QoS, no controller needed
```

## Experiments

```bash
sudo env PYTHONPATH=. python3 eval/run_experiments.py              # all modes and scenarios
sudo env PYTHONPATH=. python3 eval/run_experiments.py --only congested
python3 eval/analyze.py && python3 eval/plot.py                    # results -> summary.csv and graphs
```

Results are written to `eval/results/<mode>/<scenario>/run<k>.csv`. All figures in the report come from
`eval/results/summary.csv`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Unable to contact the remote controller at 127.0.0.1:6653` | The controller is not running, or `sudo mn -c` killed it. Clean up first, then start the controller, then the topology. |
| Ryu seems to listen on the wrong port | Ryu 4.x defaults to OpenFlow port **6653**, not 6633. |
| `OVSSwitch has no attribute 'OVSVersion'` | Fixed in `topo.py`; do not create a bare `Node` before calling `OVSSwitch.setup()`. |
| `sudo` rejects the right password after pasting commands | A pasted multi-line block fed later lines into the password prompt. Run `sudo -v` on its own first. |
| Ping fails on the `triangle` with a plain learning switch | The triangle has a loop; use the project controller, which routes loop-free. |
