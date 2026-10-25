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

Measured by the controller itself and written to `eval/results/controller_events.jsonl`:
`flow_install` (event received to barrier reply on all switches) and `reroute_done` (port down to barrier reply).

## Baseline mode

`CONTROLLER_MODE=baseline` installs only the two default rows, learns hosts, and routes by shortest path computed once.
It never gates, queues, meters, monitors, or reacts to link failures. Same topology, same queues, same traffic; only the
controller differs, which is what makes the comparison fair.

## How to test

```bash
make test                      # 20 unit tests (needs Ryu importable)
bash controller/verify_live.sh # against a running controller + Mininet; prints PASS/FAIL per rule family
```

## Known limits (say these before the examiner does)

- Policy is per host IP, not per user or room (see decision 4).
- Routing is shortest path, not load-aware.
- Meters and queues need Open vSwitch support; if unavailable the fallback is ingress policing (set up by `topology/qos.py`).
- The controller is a single process with no failover.
