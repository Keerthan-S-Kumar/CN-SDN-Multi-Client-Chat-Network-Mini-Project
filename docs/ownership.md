# Ownership & task split (D1 + D2)

**Team:** A = Janhvi · B = Keerthan · C = Harsha

**Rule that prevents merge pain:** each person owns whole files; nobody edits another person's files.
`docs/`, `common/`, `tools/` are frozen shared files — change only by PR approved by all 3.
Everyone codes against `docs/interfaces.md` and tests alone with the mocks; integration = turning mocks off.

| Person | Owns (only they edit) |
|---|---|
| **A — Chat + load + analysis** | `chat/server.py client.py bot.py spam.py`, `eval/analyze.py`, `eval/plot.py` |
| **B — Controller + repo admin** | everything in `controller/` (`app.py flows.py routing.py policy.py monitor.py rest_api.py policy.json`); GitHub repo, access, CODEOWNERS, issues, tags (`tools/setup_repo.sh`) |
| **C — Network, experiments, docs** | `topology/topo.py qos.py`, `eval/run_experiments.py`, `tests/`, `README.md`, `docs/architecture.md` |

Already written and frozen: `chat/protocol.py`, `chat/notifier.py`, `common/constants.py`, `tools/*`.

## D1: single switch, static gating and one live event

**A:** `server.py` (threaded accept loops 5000–5003, users/tokens/rooms, REGISTER/LOGIN/JOIN/LEAVE/LIST, ATTACH+MSG_ROOM, ATTACH+MSG_PM, notify on register/login/join/leave/disconnect and wait on register/login), `client.py` CLI.
*Done when:* 3 localhost clients register, join, chat, PM; `make mock` prints the events.

**B:** `app.py` + `flows.py` + `rest_api.py` for `single` topology: table-0 default + 3 drop rules + 5000 rule + allow rules on register/login (queues optional in D1), table-1 L2 (route by learned MAC, edge flooding), REST `/event /health /state`, structure code per-`dpid` so D2 routing slots in.
*Done when:* `tools/post_event.py register alice 10.0.0.2` makes `dump-flows` show prio-200 rules for 10.0.0.2; `disconnect` removes them.

**C:** `topo.py --topo single` (incl. root link 10.0.0.254, fixed MACs/ports), `tests/test_protocol.py`, `tests/test_chat_e2e.py`, `tests/demo.sh`, `docs/architecture.md`, README.
*Done when:* `pingall` 0% loss and `curl 10.0.0.254:8080/health` works from a host.

**D1 demo (the test script):** pingall → unregistered client blocked on 5001 → register → flows appear → 3 clients chat + PM → `dump-flows` per-class counters.

## D2: triangle, queues, dynamic behaviour, evaluation

**A:** `server.py` additions: port 5003 `BULK`, spam detection → `flag`/`unflag`, bad-login counting → `block`. `bot.py` (load generator, CSV per contract §11, optional spammer mode). `spam.py` (detector helper). `analyze.py`, `plot.py`.
*Done when:* 100 bots run on localhost without errors; CSV matches the schema; a spammer bot triggers `flag` at the mock.

**B:** `routing.py` (BFS over `TRIANGLE_LINKS`, edge flooding, link-down/up handling, baseline static mode), `policy.py` (VIP/banned, `/policy`), `monitor.py` (port-stats poller, congestion/heavy-hitter logic), `flows.py` additions (queues, meters, VIP/throttle/block/heavy rules), `/metrics`, controller JSONL log, `CONTROLLER_MODE=baseline`.
*Done when:* each row of the §10 table can be triggered by hand (`post_event.py`, `iperf`, `ifconfig down`) and shows in `/metrics`, `dump-flows` and the log.

**C:** `topo.py --topo triangle`, `qos.py` (OVS QoS + 4 queues on s1:1 — see §9), `run_experiments.py`, demo script for D2, final documentation/report assembly.
*Done when:* `run_experiments.py --only idle` produces CSVs for both modes.

## Git workflow
- `main` protected; PR + 1 review; squash-merge. Branches `a/…`, `b/…`, `c/…`. Small PRs, merge early.
- Tag `d1` at the D1 submission commit; D2 continues on `main`.
- Pull `main` before each session; no force-push.
