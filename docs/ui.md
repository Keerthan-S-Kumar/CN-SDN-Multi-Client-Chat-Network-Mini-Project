# UI (owner: Keerthan)

Two optional pages. Neither changes the frozen contract; if either breaks, the chat and the controller keep working.

## 1. Live SDN dashboard (served by the controller)

Open `http://<controller-ip>:8080/ui` (on the VM: `http://127.0.0.1:8080/ui`). It polls `/state`, `/metrics`, `/snapshot`
and `/events` once a second and shows: server-link utilisation against the 50 % / 80 % thresholds with a 90 s trace,
which hosts are allowed / VIP / throttled / rate limited / blocked, rooms, inter-switch link status, rule-install and
reroute times, and a plain-English feed of what the controller just did. Good for D1: register a user, flag a spammer,
or run `iperf3` and watch the page react.

Code: `controller/ui_api.py` (routes `/ui`, `/events`, `/snapshot`; registered in a try/except so it can never take the
event API down), `controller/ui/dashboard.html`, `App.snapshot()` in `controller/app.py`.

## 2. Web chat client

```bash
make web-chat                      # python3 chat/web_gateway.py --server 10.0.0.1 --port 8000
# or inside Mininet:  h2 python3 chat/web_gateway.py --server 10.0.0.1 --port 8000 &
```

Open `http://localhost:8000` (or the host's address). Each signed-in browser gets its own real TCP connections to ports
5000, 5001 and 5002 of the chat server, with the same protocol as `chat/client.py`. The server therefore sees the
gateway host's IP, so the SDN gate, queues, spam throttle and blocks apply to it like any other client. Users on one
gateway share that IP (per-IP policy, see `docs/controller.md`).

Tests: `python3 -m pytest tests/test_web_gateway.py` (real server, real gateway, driven over HTTP) and
`python3 -m unittest tests.test_ui_api`.
