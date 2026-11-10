# Interface Contract v2.1 (D1 + D2 + file sharing) — FROZEN: change only by PR approved by all 3

Everything two people must agree on lives here or in `common/constants.py`.
If code and this file disagree, this file wins. D2 only **adds** to D1; nothing D1 builds is thrown away.

## 1. Network layout (`topology/topo.py --topo single|triangle`)

Hosts: `h1`=10.0.0.1 (chat server), `h2`=10.0.0.2, `h3`=10.0.0.3, `h4`=10.0.0.4. MAC of `hN` = `00:00:00:00:00:0N`.
Controller host-side REST address: `http://10.0.0.254:8080` (root-namespace interface, MAC `00:00:00:00:00:fe`).
Controller binds REST on `0.0.0.0:8080`, OpenFlow on `127.0.0.1:6653`. Env `CONTROLLER_URL` overrides the URL (tests off-Mininet).

**single (D1)** — one switch `s1` (DPID 1): ports h1=1, h2=2, h3=3, h4=4, root/mgmt=5.

**triangle (D2)** — switches `s1,s2,s3` (DPID 1,2,3), explicit port numbers:

| Switch | Port | Connects to |
|---|---|---|
| s1 | 1 | h1 (server) — **bottleneck, see §9** |
| s1 | 2 / 3 | s2:2 / s3:3 |
| s1 | 4 | root/mgmt (10.0.0.254) |
| s2 | 1 / 2 / 3 | h2 / s1:2 / s3:4 |
| s3 | 1 / 2 / 3 / 4 | h3 / h4 / s1:3 / s2:3 |

Links in `common.constants.TRIANGLE_LINKS`. Inter-switch and host links 100 Mbps (`TCLink`, bw=100).
**Edge port** = any switch port that is not in `TRIANGLE_LINKS`. The controller learns host locations
from packet-ins on edge ports only; inter-switch links come from the constants and port-status events (no LLDP).

## 2. Ports = traffic classes

| Port | Class | Gated? | Queue (§9) |
|---|---|---|---|
| 5000 | control | No — always open (new users must register) | Q_HIGH |
| 5001 | room chat | Yes | Q_NORMAL |
| 5002 | private msg | Yes | Q_HIGH |
| 5003 | bulk | Yes | Q_BULK |

Server listens on all four. A client uses one TCP connection per port it needs.

## 3. Wire protocol (`chat/protocol.py` implements it — do not reimplement)

Frame = 4-byte big-endian length + UTF-8 JSON object, max 64 KiB. Requests carry `"id"` (int); the reply echoes it.
Reply: `{"id":n,"type":"OK",...}` or `{"id":n,"type":"ERR","code":"<CODE>","msg":"..."}`. Pushes have no id: `{"type":"MSG",...}`.

**Port 5000:** `REGISTER{user,password}`→`token` (also logs in) · `LOGIN{user,password}`→`token` ·
`JOIN{room}`→`members`,`history` (the room's last 20 messages, oldest first, each shaped like a room `MSG` push; `[]` for a new room) · `LEAVE{room}` · `LIST{what:"rooms"|"users"}`→`items`.
One active session per account: a `LOGIN` with the correct password for an account that is already online is refused with `ALREADY_ONLINE`, and the first session is not affected.

**Ports 5001/5002:** first frame `{"id":1,"type":"ATTACH","token":T}`. Then
5001 `MSG_ROOM{room,text}` → push to members `{"type":"MSG","kind":"room","room","from","text","ts"}`;
5002 `MSG_PM{to,text}` → push to recipient `{"type":"MSG","kind":"pm","from","text","ts"}`.
(Bots may add optional `"seq"` to a message; the server copies it into the push.)

**Port 5003 (bulk):** `ATTACH` then `BULK{size}`: server replies `OK` then sends one `{"type":"BULK_DATA","data":"<size bytes of 'x'>"}`
frame (size ≤ 60000). Used to generate low-priority class traffic.

**File sharing (port 5003, additive in v2.1, same frame format).** Files are held in server memory (at most 50 files and 64 MB; oldest dropped first).
- `FILE_UP{name,size,room|to}` (exactly one target; `size` 1..`MAX_FILE` = 5 MiB; sender must be in the room, or the recipient online) → `OK{fid,chunk}`.
- then the client sends `FILE_CHUNK{fid,seq,data,last}` frames in order (`data` = base64 of at most `FILE_CHUNK` = 32 KiB, so a frame stays under 64 KiB). Chunks get no reply; after the chunk with `"last":true` the server replies `OK{fid,name,size}` (or an `ERR`), then pushes a normal `MSG` to the room members or the recipient on 5001/5002 with an extra field `"file":{"fid","name","size"}` and `text` = `"[file] name (N bytes)"` (so clients that ignore `file` still show something readable). A file counts as one message for the spam rule.
- `FILE_GET{fid}` → `OK{fid,name,size,chunks}` followed by that many `FILE_CHUNK` frames. Allowed for the sender, the PM recipient, or any current member of the room.
- New error codes: `NO_SUCH_FILE`, `NOT_ALLOWED`. File names are reduced to a plain name (no path, safe characters, 80 chars).
- The bytes travel on port 5003, so the existing rules already classify them as bulk traffic (Q_BULK).

Error codes: `BAD_REQUEST USER_EXISTS BAD_CREDENTIALS NOT_LOGGED_IN NO_SUCH_ROOM NOT_IN_ROOM NO_SUCH_USER OFFLINE BAD_TOKEN NO_SUCH_FILE NOT_ALLOWED ALREADY_ONLINE`.
Names `[A-Za-z0-9_]{1,16}`; rooms created on first JOIN; `ts` = unix seconds (float).

## 4. Server → controller events (`chat/notifier.py`)

`POST {CONTROLLER_URL}/event`, JSON, **synchronous, 1 s timeout**, failure logged and ignored.

| event | extra fields | sent by server when |
|---|---|---|
| `register`, `login`, `disconnect` | — | after success / when user's 5000 connection closes |
| `join`, `leave` | `room` | user joins / leaves a room |
| `flag`, `unflag` | `reason` (`"spam"`) | user exceeds `SPAM_MSGS` messages in `SPAM_WINDOW` s / is quiet for `SPAM_QUIET` s |
| `block` | `reason` (`"bad_login"`) | `MAX_BAD_LOGINS` consecutive BAD_CREDENTIALS from one IP |

Common fields: `event, user, ip, ts` (`ip` = peer IP of the user's 5000 connection; for `block`, `user` may be `""`).
Reply `200 {"ok":true}`; bad event `400 {"ok":false,"error":..}`. `register`/`login` are sent and awaited **before** replying to the client.
The server does not limit flagged users itself; the network (meter) does the throttling.

**Controller REST:** `GET /health` · `GET /state` → `{"registered":{ip:user},"rooms":{room:[user]},"flagged":[ip],"blocked":[ip]}` ·
`GET /metrics` (§10) · `GET /policy` · `POST /policy` (same JSON as `controller/policy.json`, reloads).

## 5. Flow-table contract (`controller/`)

Table 0 = policy, table 1 = routing. `sdn` mode installs everything below; `baseline` mode installs **only** the prio-0 rows
(no gating, no queues, no meters, no reaction). Cookie = base + (port − 5000).

| Tbl | Prio | Match | Actions | Cookie |
|---|---|---|---|---|
| 0 | 0 | any | goto 1 | 0 |
| 0 | 60 | ip, `ipv4_src`=heavy host (§10) | meter `METER_HEAVY`, set_queue Q_OTHER, goto 1 | 0x9000 |
| 0 | 100 | ip, tcp, `tcp_dst`=5001/2/3 | drop | 0x5001–3 |
| 0 | 150 | ip, tcp, `tcp_dst`=5000 | set_queue Q_HIGH, goto 1 | 0x5000 |
| 0 | 200 | ip, tcp, `ipv4_src`=registered ip, `tcp_dst`=5001/2/3 | set_queue `QUEUE_BY_PORT[port]`, goto 1 | 0x5001–3 |
| 0 | 250 | as 200 but `tcp_dst`=5001, ip is VIP (§8) | set_queue Q_HIGH, goto 1 | 0x6001 |
| 0 | 300 | as 200, ip is flagged | meter `METER_THROTTLE`, set_queue Q_BULK, goto 1 | 0x7001–3 |
| 0 | 400 | ip, tcp, `ipv4_src`=blocked ip, `tcp_dst`=5000/1/2/3 | drop | 0x8000–3 |
| 1 | 0 | any | send to controller | — |
| 1 | 10 | `eth_dst` (per switch) | output (next-hop port) | — |

Table-1 route flows carry cookie `0x1000` so they can be cleared without touching the table-miss rule.
Always delete rules with an exact cookie **and** `ipv4_src` match, never a loose delete. Per-IP rules are installed on **every** switch; rules are per IP, so users sharing one host IP share its policy.
Meters (installed on every switch at connect): `METER_THROTTLE`=1 at `THROTTLE_KBPS`, `METER_HEAVY`=2 at `HEAVY_KBPS`, each one drop band.

## 6. Entry points

| What | Command |
|---|---|
| Controller | `CONTROLLER_MODE=sdn\|baseline PYTHONPATH=. ryu-manager controller/app.py` |
| Topology | `sudo python3 topology/topo.py --topo single\|triangle [--cli]` |
| Server | `python3 chat/server.py --host 0.0.0.0` |
| Client | `python3 chat/client.py --server 10.0.0.1 --user alice` |
| Bots | `python3 chat/bot.py --server 10.0.0.1 --users 10 --prefix h2u --room general --rate 2 --duration 30 --out FILE.csv` |
| Experiments | `sudo python3 eval/run_experiments.py [--only SCENARIO]` |
| Analysis | `python3 eval/analyze.py && python3 eval/plot.py` |

## 7. Routing (table 1) — loop-free, deterministic

- **Known destination:** BFS shortest path over `TRIANGLE_LINKS` minus down links (ties → lower DPID); install one prio-10 `eth_dst` flow on every switch on the path.
- **Broadcast / unknown destination (ARP etc.):** controller packet-out to **edge ports only**, on every switch, excluding the ingress port. Never out inter-switch ports (no loops).
- **Link down** (`PortStatus` with link down on an inter-switch port): mark link down, delete all prio-10 flows on all switches, re-learn lazily; log `link_down` then `reroute_done`. **Link up:** mark up, delete prio-10 flows, log `link_up`.
- `baseline`: same BFS on first packet-in, but **never** recomputed on link events (static).

## 8. Policy (`controller/policy.json`)

```json
{"vip_users": ["alice"], "vip_rooms": ["vip"], "banned_users": []}
```
VIP ip = user in `vip_users` **or** a member of any `vip_rooms` (rule is per user/IP because one connection carries all rooms).
`banned_users` are treated as blocked on `register`/`login` for as long as they stay listed. `unflag`/block expiry (`BLOCK_SECONDS`) are timed by the controller.

## 9. Bottleneck and queues (`topology/qos.py` configures; controller only selects `set_queue`)

Only `s1` port 1 (toward h1) has QoS: `linux-htb`, max 10 Mbps. This is the shared bottleneck for all client→server traffic.

| Queue | Name | min | max | priority |
|---|---|---|---|---|
| 0 | `Q_OTHER` (non-chat, e.g. iperf; and everything in baseline) | 1 Mbps | 10 | 5 |
| 1 | `Q_HIGH` (control, PM, VIP) | 3 Mbps | 10 | 1 |
| 2 | `Q_NORMAL` (room chat) | 3 Mbps | 10 | 2 |
| 3 | `Q_BULK` (bulk, throttled users) | 0.5 Mbps | 3 | 6 |

## 10. Dynamic behaviour, thresholds, metrics

Monitor polls port stats every `MONITOR_INTERVAL` s (`sdn` only).

| Trigger | Detection | Action | Undo |
|---|---|---|---|
| Congestion | egress `tx_bytes` rate on s1:1 ≥ `CONGEST_ON`×10 Mbps for `CONGEST_SAMPLES` samples | find host with edge-port `rx` rate ≥ `HEAVY_SHARE`×10 Mbps; install prio-60 heavy rule for its IP | rate ≤ `CONGEST_OFF` → delete heavy rules |
| Spam | `flag` event | prio-300 throttle rules for the IP | `unflag` |
| Unauthorized | unregistered IP on 5001–3 | prio-100 drop (static) — count hits in `/metrics` | register |
| Brute force | `block` event or banned user | prio-400 drop for IP | after `BLOCK_SECONDS` |
| Link failure | port-status down on inter-switch port | reroute (§7) | link up |

`GET /metrics`: `{"mode","congested":bool,"util":float,"throttled":[ip],"heavy":[ip],"blocked":[ip],"links_down":[[a,b]],"drop_hits":int,"last_flow_install_ms":float,"last_reroute_ms":float}`.

Controller appends JSON lines to `eval/results/controller_events.jsonl`: `{"t":unix,"kind":K,"detail":{}}`,
K ∈ `event_rx flow_install link_down reroute_done link_up congestion_on congestion_off heavy_on heavy_off throttle_on throttle_off block_on block_off`.
`flow_install.detail = {"trigger":ev,"ms":float}` = event received → barrier reply. `reroute_done.detail = {"ms":float}` = port-down → barrier reply of last flow delete.

## 11. Evaluation data formats (see `docs/eval-plan.md`)

Bot CSV columns: `t_send,t_recv,sender,receiver,kind,room,bytes` (`t_recv` empty = lost). Results path:
`eval/results/<mode>/<scenario>/run<k>.csv` (+ copy of controller log). Modes `sdn`,`baseline`; scenarios `idle congested flood linkfail scale5 scale10 scale25 scale50 scale100`.
