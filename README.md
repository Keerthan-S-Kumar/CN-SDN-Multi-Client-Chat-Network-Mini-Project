# Multi-Client Chat Network over SDN (UE25CS243A — Jackfruit mini project)

TCP chat (registration, rooms, private messages) on a Mininet network. A Ryu controller classifies chat traffic by port, gates and prioritises it, reacts to chat events (spam, brute force), congestion and link failure, and is evaluated against a static baseline.

**Team:** Janhvi (chat app) · Keerthan (controller) · Harsha (network, experiments, docs)

- Contract between components: [`docs/interfaces.md`](docs/interfaces.md)
- Who owns what, D1/D2 tasks and schedule: [`docs/ownership.md`](docs/ownership.md)
- Experiments and metrics: [`docs/eval-plan.md`](docs/eval-plan.md)

## Run (Ubuntu 22.04 VM)
```bash
make controller MODE=sdn            # terminal 1 (Ryu)
make topo TOPO=single               # terminal 2 (Mininet CLI)  — TOPO=triangle for D2
mininet> h1 python3 chat/server.py --host 0.0.0.0 &
mininet> xterm h2 h3                # python3 chat/client.py --server 10.0.0.1 --user alice
make flows                          # classification / policy flows
make metrics                        # controller state (D2)
```
Experiments: `make experiments && make report`.  *(C: expand once everything runs.)*
