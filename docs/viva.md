# Viva preparation: questions and honest answers

Each member should be able to answer every question here, not only their own part. Numbers come from
[`results.md`](results.md) (3 runs per mode unless stated).

## The idea

**What problem does the project solve?** A chat service shares one network with other traffic. A plain network treats
every packet the same, so a flood or a failed link hurts everyone. With SDN the controller sees chat events (who
registered, who is spamming) and changes the switch rules, so the network reacts to the application.

**Why TCP and not UDP?** Chat and file messages must arrive complete and in order, and TCP gives that. We use one TCP
connection per traffic class (ports 5000 to 5003) so the switch can classify traffic by destination port alone.

**Draw the architecture.** Clients and the chat server sit on Mininet hosts. Open vSwitch switches carry the traffic.
The Ryu controller talks OpenFlow 1.3 to the switches and receives events from the chat server over REST
(`POST /event`). The dashboard and browser chat read the controller's REST API.

## Sockets

**How do you separate messages inside a TCP stream?** Each message is a 4-byte big-endian length followed by JSON
(`chat/protocol.py`). Without it two messages could merge, or one could arrive in pieces.

**How does the server handle many clients?** One thread per connection, shared state (users, rooms, connections)
behind one lock. Each connection has its own send lock so two threads never interleave frames.

**What is the token for?** `REGISTER` or `LOGIN` on port 5000 returns a token. The other three connections send
`ATTACH` with it, which ties them to the same user. The server then knows which user a room message came from.

**How do file transfers work?** `FILE_UP` announces name, size and the target (a room or a user). The file follows
as base64 chunks of 32 KiB (so a frame stays under the 64 KiB limit) and the server replies after the last chunk.
Recipients get a normal message with a `file` field and download with `FILE_GET`. Only the sender, the private-chat
recipient or a current room member may download. The limit is 5 MB and 50 files in memory.

**How are errors handled?** Every request gets `OK` or `ERR` with a code (for example `NO_SUCH_ROOM`, `OFFLINE`,
`NOT_ALLOWED`). A bad frame closes that connection only. Passwords are stored as salted PBKDF2 hashes.

## SDN

**What is a flow table and how are yours organised?** A flow table is a list of match and action rules. Table 0 is
policy (gate, classify, throttle, block) and table 1 is routing. Higher priority wins: 400 block, 300 throttle,
250 VIP, 200 allow, 100 gate drop, 60 heavy-host meter, 0 default.

**What does "gating" mean?** Until a host registers, packets to ports 5001 to 5003 are dropped at the switch (priority
100). Port 5000 stays open so a new user can register. Registering makes the server tell the controller, which installs
allow rules for that host's IP on every switch.

**Queue or meter: what is the difference?** A queue (HTB on the 10 Mbps bottleneck port) shares bandwidth between
classes and gives priority. A meter drops traffic above a rate. We use queues for classification and meters for
punishment (spam: 100 kbit/s, heavy host: 2 Mbit/s).

**Why is the bottleneck on one port?** Every client reaches the server through switch s1 port 1, so that is where
congestion appears. Queues are configured only there.

**How does the controller detect congestion?** It reads port counters every second. Above 80% of 10 Mbps for 2
samples marks congestion and the host sending over 30% of the link gets a meter. Below 50% the meter is removed.

**How does it recover from a link failure?** A port-status message tells it a link is down. It deletes the route
flows, recomputes shortest paths without that link and installs new ones on demand. Baseline mode never recomputes.

**What are the controller's limits?** Rules are per host IP, not per user, so two users on one host share a policy.
It is one process with no failover. Routing is shortest path, not load-aware.

## Integration

**Show that the application changes the network.** Register a user and `make flows` shows three new priority-200 rules
for that IP within milliseconds (the dashboard shows the install time). Send 30 messages quickly: the server sends
`flag`, and priority-300 throttle rules appear. Five wrong passwords: `block` and priority-400 drops.

**What happens if the controller is down?** The server logs the failed POST and carries on (1 second timeout). Chat
works for hosts that already have rules; new hosts stay gated.

## Measurements

**What did you measure and against what?** Message latency (p50, p95, p99), loss and recovery time in idle, congested,
flood, link-failure and 5 to 100 client runs, SDN mode against a static baseline controller (same topology, no queues,
meters or reaction).

**What are the main results?** Congested link: p50 3.5 ms with SDN against 164.8 ms for the baseline. Link failure:
p95 35 ms against 11.4 s, because the baseline never reroutes. Idle: both about 4 ms, so SDN adds no cost.

**Where did SDN not help?** The single-host flood: the baseline's median was lower (2.1 ms against 3.6 ms), so
throttling at the switch did not protect latency there. At 100 clients both modes are overloaded and SDN lost more
messages (33.8% against 14.4%). We did not isolate the cause; the VM and the single-process server are the likely
limits. Say this plainly: it is a finding, not a failure to hide.

**Why 3 runs?** The VM is small and each run is slow. Three runs show the spread without taking hours. More runs
would tighten the numbers.

## Questions that catch people out

- **Is the dashboard live or recorded?** Live: it polls the controller every second. The Results tab reads the saved
  `summary.csv`.
- **Who can press the dashboard buttons?** Only a request from the controller machine itself; hosts in the network
  get a 403.
- **Why did you not do voice chat?** We decided file sharing gave us real bulk traffic to measure and left time to
  test it. Voice is a possible extension (it would need a UDP port and its own priority queue).
- **What did you personally build?** See the table in the README; be ready to open that code and explain a function.
