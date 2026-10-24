#!/usr/bin/env bash
# Run once from the repo root after `gh auth login`. Creates labels and all D1 + D2 issues.
set -e
for l in A B C; do gh label create "owner-$l" --force >/dev/null; done
gh label create D1 --force >/dev/null
gh label create D2 --force >/dev/null

mk() { gh issue create --title "$1" --label "$2" --label "$3" --body "$4" >/dev/null; echo "created: $1"; }

# ---- D1 ----
mk "A: server core (accept loops, users, rooms, tokens)"       owner-A D1 "docs/ownership.md D1-A"
mk "A: server notifier hooks (wait on register/login)"          owner-A D1 "interfaces §4"
mk "A: client CLI"                                              owner-A D1 "client.py docstring"
mk "B: controller tables 0/1 + L2 + per-dpid state"             owner-B D1 "interfaces §5, §7"
mk "B: REST /event /health /state"                              owner-B D1 "interfaces §4"
mk "B: allow/drop rules per registered IP"                      owner-B D1 "register/login add, disconnect remove"
mk "C: topo --topo single with root link, fixed MACs/ports"     owner-C D1 "interfaces §1"
mk "C: tests (protocol, e2e, demo.sh)"                          owner-C D1 "5-step demo"
mk "C: architecture doc + README + repo setup"                  owner-C D1 "2 pages + diagram"
mk "ALL: D1 integration, rehearsal, tag d1"                     owner-C D1 "checkpoints in ownership.md"
# ---- D2 ----
mk "A: BULK on 5003"                                            owner-A D2 "interfaces §3"
mk "A: spam detector -> flag/unflag; bad logins -> block"       owner-A D2 "interfaces §4"
mk "A: bot.py load generator + CSV"                             owner-A D2 "interfaces §11"
mk "A: analyze.py + plot.py"                                    owner-A D2 "eval-plan.md"
mk "B: routing.py BFS, edge flooding, link down/up"             owner-B D2 "interfaces §7"
mk "B: queues/meters/VIP/throttle/block/heavy rules"            owner-B D2 "interfaces §5"
mk "B: monitor.py congestion + heavy hitter"                    owner-B D2 "interfaces §10"
mk "B: policy.py, /policy, /metrics, JSONL log"                 owner-B D2 "interfaces §8, §10"
mk "B: baseline mode"                                           owner-B D2 "interfaces §5, §7"
mk "C: topo --topo triangle"                                    owner-C D2 "interfaces §1"
mk "C: qos.py queues on s1:1"                                   owner-C D2 "interfaces §9"
mk "C: run_experiments.py"                                      owner-C D2 "eval-plan.md"
mk "C: D2 demo script + final report assembly"                  owner-C D2 "each person writes own section"
