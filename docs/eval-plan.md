# Evaluation plan (D2: Performance 5 marks + Robustness 4 marks)

**Comparison:** `baseline` (static shortest-path switching, no gating/queues/meters/reaction) vs `sdn` (full controller),
on the **same triangle topology and same QoS queues**. Only the controller mode changes.

## Scenarios (30 s each, 3 runs, 10 bots unless stated)

| Scenario | Setup | What it shows |
|---|---|---|
| `idle` | bots only, h2/h3/h4 | overhead of SDN (should be ≈ baseline) |
| `congested` | + `iperf` UDP 12 Mbps h3→h1 (port 5201) | queues/heavy-hitter meter protect chat latency |
| `flood` | + one spammer bot (200 msg/s) | flag → throttle protects other users |
| `linkfail` | `s1–s2` taken down at t=10 s, restored at t=20 s | rerouting vs black-hole; recovery time |
| `scale5…scale100` | N bots spread over h2,h3,h4, no background | scalability: latency/throughput vs N |

Also demonstrated live (robustness): unauthorized access (unregistered client dropped), brute-force → `block`.

## Metrics

| Metric | Source |
|---|---|
| Message latency p50/p95/p99 (ms) | bot CSV: `t_recv - t_send` |
| Throughput (msgs/s delivered) | bot CSV |
| Loss % | rows with empty `t_recv` |
| Flow install time (ms) | controller log `flow_install` |
| Failover time (ms) | controller log `reroute_done`; cross-check with client-side gap |
| Controller reaction time to congestion | `congestion_on` timestamp − iperf start |

## Outputs (`eval/`)

- `run_experiments.py` (C): for each mode × scenario × run → restart controller with `CONTROLLER_MODE`, build triangle topology, start server, run bots/iperf/link events, collect CSV + controller log into `eval/results/<mode>/<scenario>/run<k>.csv`.
- `analyze.py` (A): CSVs → `eval/results/summary.csv` (mean ± std per mode/scenario).
- `plot.py` (A): `latency_bar.png` (p50/p95/p99 per scenario, sdn vs baseline), `scalability.png` (latency & throughput vs N), `linkfail_timeline.png`, `congestion_cdf.png`.
- All numbers in the final report must come from `summary.csv`.
