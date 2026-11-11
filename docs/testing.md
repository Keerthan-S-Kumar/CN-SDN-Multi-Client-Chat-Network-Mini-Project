# Testing

Four layers: unit tests, a live check of the controller, scenario runs on the triangle topology, and the manual demo steps.

## 1. Automated tests

| Area | Files | What it checks |
|---|---|---|
| Chat server | `tests/test_chat_server.py` | registration, login, rooms, messages, and the `block` event after repeated bad logins |
| Web gateway and UI API | `tests/test_web_gateway.py`, `tests/test_ui_api.py` | the browser chat gateway and the controller's dashboard API |
| Controller logic | `controller/test_logic.py` | gating, queues, throttling, rerouting and the monitor, against the controller's own functions |
| Topology | `tests/test_topology.py`, `tests/test_triangle.py` | host addresses, port numbers, links, switch IDs and the QoS queues (need root and Mininet) |

Latest results (the chat, gateway and dashboard tests also run on every push through GitHub Actions):

| Command | Result |
|---|---|
| `python3 -m pytest tests/test_chat_server.py tests/test_web_gateway.py tests/test_ui_api.py -q` (no Ryu or Mininet needed) | 59 passed, 2 skipped |
| `python3 -m unittest controller.test_logic` (Ryu environment) | 23 passed |
| `sudo env PYTHONPATH=. python3 -m unittest tests.test_topology tests.test_triangle` (root, Mininet) | run on the project VM |

The earlier VM run before file sharing and the dashboard changes was 71 passed in the Ryu environment; rerun it after pulling.

File sharing is covered by the same suites: `tests/test_chat_server.py` (upload to a room and a private chat, access checks, bad sizes, out-of-order chunks, salted passwords, account persistence) and `tests/test_web_gateway.py` (browser upload and download, outsider refused, 5 MB limit). `eval/file_transfer.py` measures throughput on the real network.

## 2. Live check of the controller

`controller/verify_live.sh` runs against a live Mininet. It posts events to the controller and checks the OpenFlow rules that appear and disappear on every switch: the static gate (3 drop rules and 1 control rule), allow rules after register, the VIP rule for a VIP room, throttle rules after a flag, 4 block rules after a block event, and removal of all of them afterwards.

## 3. Scenario runs

`eval/run_experiments.py` runs each scenario against both modes (`sdn`, `baseline`) and writes `eval/results/<mode>/<scenario>/run<k>.csv`. The scenarios and the numbers are in `docs/results.md`.

| Scenario | What is tested | Evidence |
|---|---|---|
| Congestion | a 12 Mbps UDP stream into the 10 Mbps bottleneck | median latency 3.5 ms (sdn) against 165 ms (baseline) |
| Link failure | link s1-s2 down at 10 s, up at 20 s | p95 35 ms (sdn) against 11,355 ms (baseline); no loss |
| Flood | one spammer at 200 messages per second | no improvement in latency (see results doc) |
| Flood, large messages | one spammer sending 4000-byte messages, 200 per second | p95 27 ms (sdn) against 71 ms (baseline) |
| Scale | 5 to 100 bots | same up to 50 bots; both modes overloaded at 100 bots, with very variable runs |
| Idle | overhead of the controller | equal to baseline within noise |

## 4. Manual scenarios (demo)

| Scenario | How it is shown | Expected |
|---|---|---|
| Unauthorized access | a host that has not registered connects to port 5001 | the connection times out because the switch drops the packet; port 5000 stays open |
| Registration opens the gate | register a user, then `ovs-ofctl dump-flows s1` | three priority-200 allow rules for the host, one per port, each with a queue |
| Disconnect closes the gate | the user disconnects | the allow rules are removed and the drop rules remain |
| Brute force | repeated bad logins from one host | the server sends a `block` event and the controller installs 4 drop rules (ports 5000 to 5003) |

The first three steps are in `docs/demo-d1.md`; the block rules are also checked by `controller/verify_live.sh`.

## Run everything

    python3 -m pytest tests -q
    $HOME/ryu-env/bin/python3 -m pytest tests controller/test_logic.py -q --ignore=tests/test_topology.py --ignore=tests/test_triangle.py
    bash controller/verify_live.sh      (needs the controller and a topology running)

## Known gaps

- At 100 bots both modes are overloaded and the runs vary widely, so no conclusion is drawn there; scale 5, 10 and 25 have one run per mode.
- The flood scenario shows no latency gain from throttling; see `docs/results.md`.
- Rules are per host IP, so one flagged user throttles every user on the same host.
