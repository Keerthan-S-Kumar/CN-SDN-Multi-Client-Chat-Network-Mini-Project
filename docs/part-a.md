# Part A: Chat application, load generator and evaluation tools

Files: `chat/server.py`, `chat/client.py`, `chat/spam.py`, `chat/bot.py`, `eval/analyze.py`, `eval/plot.py`.
Shared, frozen files used but not changed: `chat/protocol.py` (framing), `chat/notifier.py` (events), `common/constants.py`.

## 1. Server design (`chat/server.py`)

- **Ports as traffic classes:** the server listens on 5000 (control), 5001 (room chat), 5002 (private messages) and 5003 (bulk). A client uses one TCP connection per port, so the network can classify traffic by TCP destination port.
- **Threads:** one listener thread per port, one thread per client connection, and one background thread (`spam_watch`) that unflags quiet users.
- **Shared state:** users, tokens, online users, rooms and the attached connections of each port live in dictionaries protected by one lock. Each connection also has its own send lock, so two threads pushing messages to the same client cannot interleave their frames.
- **Login flow:** `REGISTER` or `LOGIN` on port 5000 returns a token. Connections to 5001, 5002 and 5003 send `ATTACH` with that token before anything else. When the 5000 connection closes, the user's token, room memberships and attached connections are removed and a `disconnect` event is sent.
- **Robustness:** a bad frame closes only that connection, errors return an `ERR` code from the contract, and every connection handler cleans up in a `finally` block, so one bad client cannot crash the server.
- **Limits:** names are `[A-Za-z0-9_]{1,16}`, frames are at most 64 KiB, bulk size is 1 to 60000, and shared files are 1 byte to 5 MiB (see "File sharing" in `docs/interfaces.md`). Passwords are stored as salted PBKDF2-SHA256 hashes; start the server with `--db accounts.json` to keep accounts between restarts.

## 2. Protocol

Length-prefixed JSON (4-byte big-endian length, then a UTF-8 object), implemented in `chat/protocol.py`.

| Port | Requests | Replies and pushes |
|---|---|---|
| 5000 | `REGISTER`, `LOGIN`, `JOIN`, `LEAVE`, `LIST` | `OK` with `token`, `members` or `items`, or `ERR` with a code |
| 5001 | `ATTACH`, `MSG_ROOM` | push `MSG` (kind `room`) to every other member of the room |
| 5002 | `ATTACH`, `MSG_PM` | push `MSG` (kind `pm`) to the recipient |
| 5003 | `ATTACH`, `BULK{size}` | `OK`, then one `BULK_DATA` frame of `size` bytes |

Error codes: `BAD_REQUEST`, `USER_EXISTS`, `BAD_CREDENTIALS`, `NOT_LOGGED_IN`, `NO_SUCH_ROOM`, `NOT_IN_ROOM`, `NO_SUCH_USER`, `OFFLINE`, `BAD_TOKEN`.

## 3. Events sent to the SDN controller

The server calls `chat.notifier.notify()` (HTTP POST, 1 s timeout, never raises) through a small `emit()` wrapper.

| Event | Sent when |
|---|---|
| `register`, `login` | after a successful registration or login, before the reply to the client |
| `join`, `leave` | a user joins or leaves a room |
| `disconnect` | the user's control connection closes |
| `flag`, `unflag` | spam detected, and after the user has been quiet for a while |
| `block` | too many bad logins from one IP |

If the controller is unreachable the failure is logged and chat continues normally.

## 4. Spam detection (`chat/spam.py`)

A sliding window per user: 20 messages within 5 s raises a `flag` event, once per episode. After 30 s without messages the user is unflagged (also on disconnect). The server does not limit flagged users itself; the network (meter) does the throttling.

The flag is per user, but the controller throttles per source IP, so every client on the same host as a spammer is throttled too. In experiments, run the spammer bot on a different host from the bots being measured.

## 5. Brute-force protection

Only failed `LOGIN` attempts count. After 5 consecutive `BAD_CREDENTIALS` from one IP the server sends a `block` event with reason `bad_login` and resets that IP's counter. A successful login also resets it. The controller applies the actual block.

## 6. Client (`chat/client.py`)

`python3 chat/client.py --server 10.0.0.1 --user alice` registers (or logs in if the name exists) and opens the three connections. Commands: `/register`, `/login`, `/join`, `/leave`, `/room`, `/msg`, `/pm`, `/sendroom <room> <file>`, `/sendpm <user> <file>`, `/get <file id> [folder]`, `/list users|rooms`, `/help`, `/quit`. Background threads print incoming messages while the main thread reads the keyboard. Anything typed without `/` goes to the current room.

## 7. Load generator (`chat/bot.py`)

```
python3 chat/bot.py --server 10.0.0.1 --users 10 --prefix h2u --room general --rate 2 --duration 30 --out FILE.csv [--spammer]
```

Each bot registers, joins the room, and sends room messages at `--rate` messages/s for `--duration` seconds. Every message text carries the sender, a sequence number and the send time. Receivers log the arrival time. The CSV has the columns `t_send,t_recv,sender,receiver,kind,room,bytes`, one row per (message, receiver), and an empty `t_recv` means the message was lost. `--spammer` adds one extra bot sending 200 msg/s whose own messages are not logged. Keep `--rate` at 3 or below, because the server flags anyone above 20 messages per 5 s. If bots run on several hosts, each process writes its own CSV and a message to a remote bot appears twice (empty and filled), so `analyze.py` merges rows by (sender, receiver, t_send).

## 8. Analysis and plots (`eval/`)

- `python3 eval/analyze.py` reads `eval/results/<mode>/<scenario>/run*.csv` and the controller logs next to them, and writes `eval/results/summary.csv`: mean and standard deviation of p50, p95, p99 latency, throughput, loss, flow install time and reroute time per mode and scenario.
- `python3 eval/plot.py` (needs matplotlib) draws `latency_bar.png`, `scalability.png`, `linkfail_timeline.png` and `congestion_cdf.png`, with `sdn` and `baseline` on every plot.

## 9. How to run on one machine

```
python3 chat/server.py --host 127.0.0.1
python3 chat/client.py --server 127.0.0.1 --user alice
python3 chat/bot.py --server 127.0.0.1 --users 5 --prefix t --rate 2 --duration 10 --warmup 1 --out /tmp/bots.csv
```

Set `CONTROLLER_URL=http://127.0.0.1:8080` and run `tools/mock_controller.py` to see events accepted instead of timing out.

On the Mininet network, `make server` starts the server on h1 (see the README's Running section); clients and bots run on h2 to h4 with `--server 10.0.0.1`.

## 10. Testing done

Scripted tests of register, join, room message and the duplicate-login error; the bulk port (valid and too-large sizes); a 25-message flood (one `flag`, then one `unflag` about 30 s later); five wrong passwords (one `block` event); and a 5-bot load run (100 messages, 400 deliveries, 0 errors, p50 about 0.9 ms on localhost).

## 11. Known limitations

State is in memory, so users are lost on restart unless the server is started with `--db accounts.json` (accounts only; rooms, messages and shared files are never saved). Passwords are stored as salted PBKDF2-SHA256 hashes. Tokens never expire, shared files are kept in memory only (at most 50 files and 64 MB), and there is no encryption on the wire. The server is one process with one thread per connection, which is fine for 100 bots but would not scale to thousands.