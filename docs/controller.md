# SDN controller (owner: Keerthan)

Ryu app, OpenFlow 1.3, files in `controller/`. Contract: [`interfaces.md`](interfaces.md) §4, §5, §7, §8, §10.

## What it does in one paragraph

The chat server tells the controller what is happening (register, login, join, leave, flag, block, disconnect) with a
small HTTP call. The controller keeps that as state and turns it into OpenFlow rules: unregistered hosts are dropped on the
chat ports, registered ones are classified into priority queues by port, VIP users get the high queue, spammers are
metered, brute-forcers are dropped, a heavy sender is metered when the server link congests, and a failed inter-switch
link is routed around. A `baseline` mode switches all of that off so the evaluation has something to compare against.

## Files

| File | Role |
|---|---|
| `app.py` | Ryu app: switch lifecycle, packet-in, event handling, reconcile loop, monitoring, barrier timing |
| `flows.py` | Builds every FlowMod/MeterMod (one function family per row of the flow table) |
| `routing.py` | Pure-python graph: BFS shortest path over the triangle minus down links |
| `monitor.py` | Pure-python congestion detector (hysteresis) and heavy-hitter selection |
| `policy.py`, `policy.json` | VIP users/rooms and banned users, reloadable at `POST /policy` |
| `rest_api.py` | `POST /event`, `GET /health /state /metrics /policy`, `POST /policy` |
| `test_logic.py` | 20 tests; real OpenFlow messages are serialised, switches are faked |

## Design decisions (and why)

1. **State first, rules second.** Events only change state (`sessions`, `rooms`, `flagged`, `blocked`, `heavy`).
   `reconcile()` then computes the rules that state implies, compares with what is installed, and sends only the
   difference. Duplicate events, two users on one host, and a switch reconnecting all become correct for free.
2. **Two tables.** Table 0 is policy (gating, queues, meters, drops), table 1 is routing. Policy never has to know about
   routes and vice versa; the baseline is simply table 0 with only its default row.
3. **Port = class.** The server listens on one port per traffic class, so classification is a plain `tcp_dst` match.
   Port 5000 is never gated, otherwise a new user could not register.
4. **Rules are per source IP.** One TCP connection carries every room a user is in, and several users can share a host,
   so a rule cannot tell users or rooms apart. VIP and throttle therefore apply to the whole host.
5. **No LLDP.** The triangle is fixed, so links come from `TRIANGLE_LINKS` and failures from port-status events. Fewer
   moving parts, deterministic paths.
6. **Loop-free flooding.** Unknown and broadcast frames are sent only out edge ports, never between switches.
7. **Strict deletes.** Rules are removed by exact priority, match and cookie, so removing one user's rules cannot touch
   anyone else's or the drop rules.

## Flow table (as installed)

| Table | Prio | Match | Action | Why |
|---|---|---|---|---|
| 0 | 0 | any | goto 1 | default |
| 0 | 60 | `ipv4_src` = heavy host | meter 2, queue 0 | congestion response |
| 0 | 100 | tcp `5001/2/3` | drop | unauthorised access |
| 0 | 150 | tcp `5000` | queue 1 | control always open, high priority |
| 0 | 200 | registered IP + tcp `5001/2/3` | queue 2 / 1 / 3 | allow + classify |
| 0 | 250 | VIP IP + tcp `5001` | queue 1 | VIP room chat |
| 0 | 300 | flagged IP + tcp `5001/2/3` | meter 1, queue 3 | spam throttle |
| 0 | 400 | blocked IP + tcp `5000..5003` | drop | brute force |
| 1 | 10 | `eth_dst` | output | routing, per switch |
| 1 | 0 | any | to controller | learn hosts |

## Dynamic behaviour

| Trigger | How it is detected | Reaction | Undo |
|---|---|---|---|
| Register / login | `register` / `login` event | allow + classify rules for that IP, on every switch | `disconnect` |
| VIP | user in `vip_users`, or member of a `vip_rooms` room | prio-250 rule | leaves room / disconnects |
| Spam | server sends `flag` (20 messages in 5 s) | meter + bulk queue for that IP | `unflag` after 30 s quiet |
| Brute force | `block` event (5 bad logins) or banned user | drop everything from that IP for 60 s | expiry timer |
| Congestion | s1 port 1 transmit rate >= 80 % of 10 Mbps for 2 samples | meter the host sending >= 30 % of the link | rate <= 50 % for 2 samples |
| Link failure | port-status down on an inter-switch port | recompute shortest paths, reinstall routes | port-status up |

Measured by the controller itself and written to `eval/results/controller_events.jsonl` (set `CHATSDN_LOG=<path>` to write a
separate file per run, which is what the experiment runner should do):
`flow_install` (event received to barrier reply on all switches) and `reroute_done` (port down to barrier reply).

## Baseline mode

`CONTROLLER_MODE=baseline` installs only the two default rows, learns hosts, and routes by shortest path computed once.
It never gates, queues, meters, monitors, or reacts to link failures. Same topology, same queues, same traffic; only the
controller differs, which is what makes the comparison fair.

## Test cases (controller)

Setup for T1-T10: `make controller` (terminal 1), `make topo TOPO=single` (terminal 2, `TOPO=triangle` for T8-T9),
chat server running on h1 (`h1 python3 chat/server.py --host 0.0.0.0 &`). Rules are read with `make flows`,
state with `curl -s localhost:8080/state`, counters and timings with `curl -s localhost:8080/metrics`.
Fill the last column when you run each case; keep the output as screenshots for the demo.

| ID | Case | Steps | Expected | Run |
|---|---|---|---|---|
| T1 | Switch connects, static rules installed | start controller, then topology | log `switch 1 connected`; `make flows` shows 3 drop rules (5001-5003, prio 100) and the 5000 rule (prio 150) | [ ] |
| T2 | Unregistered host is blocked (unauthorised access) | `h2 nc -zv -w2 10.0.0.1 5001`, then `... 5000` | 5001 times out, 5000 connects; `drop_hits` in `/metrics` goes up | [ ] |
| T3 | Registering opens the gate | register on h2 with `chat/client.py`, then retry 5001 | 5001 now connects; `make flows` shows 3 prio-200 rules for 10.0.0.2 (cookies `0x5001`-`0x5003`); `/state` lists the user | [ ] |
| T4 | Disconnect closes the gate | quit the client | prio-200 rules for 10.0.0.2 gone; 5001 blocked again; drop rules still there | [ ] |
| T5 | VIP policy | register a user listed in `policy.json` (`alice`), or join room `vip` | one prio-250 rule for that IP (queue 1); leaving the room removes it | [ ] |
| T6 | Spam throttle | `chat/bot.py --spammer` alone on h4 | `flag` event, 3 prio-300 rules with meter 1 (`make meters`); removed ~30 s after the spam stops | [ ] |
| T7 | Brute-force block | 5 wrong logins from one host | `block` event, 4 prio-400 drop rules for that IP, `/metrics.blocked` lists it; gone after 60 s | [ ] |
| T8 | Congestion response (triangle) | `iperf3 -u -b 12M` from h3 to h1 while bots chat | `/metrics.congested` true, prio-60 rule for 10.0.0.3, `congestion_on`/`heavy_on` in the event log; all clear after iperf stops | [ ] |
| T9 | Link failure and recovery (triangle) | Mininet CLI: `link s1 s2 down`, ping h2 to h1, `link s1 s2 up` | `links_down` = `[[1,2]]`, ping recovers over s3, `reroute_done` with `ms` in the log; after `up` the direct path returns | [ ] |
| T10 | Baseline behaves differently | restart with `make controller MODE=baseline`, repeat T2 and T9 | 5001 reachable without registering; no queue/meter rules; the failed link is not rerouted | [ ] |
| T11 | Unit tests | `make test` | 20 controller tests pass | [ ] |
| T12 | Live rule check | `bash controller/verify_live.sh` | every line `PASS` | [ ] |

## Rubric map (controller)

| Rubric line | Where it is shown |
|---|---|
| Mininet + SDN implementation (D1, 4) | `app.py`, `flows.py`, `routing.py`; T1, T2, T11 |
| Socket-SDN integration (D1, 3) | event to rule path in `handle_event`; T3, T4, T12 |
| Initial testing and demo (D1, 2) | T1-T4 with screenshots |
| Dynamic SDN functionality (D2, 6) | T5-T9 (prioritisation, filtering, monitoring, blocking, rerouting, recovery) |
| Challenging scenario (D2, 4) | T2 (unauthorised access), T7, T8 (congestion), T9 (link failure) |
| Performance and baseline (D2, 5) | T10 and `docs/eval-plan.md`; controller timings in `controller_events.jsonl` |

## Known limits (say these before the examiner does)

- Policy is per host IP, not per user or room (see decision 4).
- Routing is shortest path, not load-aware.
- Meters and queues need Open vSwitch support; if unavailable the fallback is ingress policing (set up by `topology/qos.py`).
- The controller is a single process with no failover.
