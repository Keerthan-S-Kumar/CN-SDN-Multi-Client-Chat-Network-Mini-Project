# D2 results: SDN controller vs static baseline

All numbers come from `eval/results/summary.csv` (made by `make report` from the runs under `eval/results/<mode>/<scenario>/`).

## Setup

- **Topology:** the three-switch triangle (s1, s2, s3), 100 Mbps links, a 10 Mbps bottleneck on the port to the chat server (h1) with four HTB queues.
- **Modes:** `sdn` is the full controller (gating, queues, heavy-hitter meter, throttling, rerouting). `baseline` is the same controller code with only static shortest-path forwarding. Same topology and same queues; only `CONTROLLER_MODE` differs.
- **Load:** 10 bots spread over h2, h3 and h4, 2 messages per second each, 30 seconds per run, one shared room. Scale runs use 5 to 100 bots; at 50 and 100 bots the rate is lowered to 1 and 0.5 messages per second per bot so the VM can keep up.
- **Runs:** 3 per mode and scenario for idle, congested, flood and linkfail; 1 per mode for the scale runs.
- **Metric:** message latency = time received minus time sent, in milliseconds (p50, p95, p99). Loss is the share of expected deliveries that never arrived.
- **Runner:** `eval/run_experiments.py` (restarts the controller, builds the topology, runs the bots, collects the CSV and the controller log for every run).

## Results

| Scenario | Mode | p50 (ms) | p95 (ms) | p99 (ms) | Loss % |
|---|---|---|---|---|---|
| idle | sdn | 4.34 | 29.1 | 53.7 | 0.0 |
| idle | baseline | 4.42 | 24.8 | 57.0 | 0.0 |
| congested | sdn | 3.46 | 19.7 | 40.6 | 0.0 |
| congested | baseline | 164.8 | 270.4 | 376.9 | 0.0 |
| flood | sdn | 3.60 | 24.2 | 49.3 | 0.0 |
| flood | baseline | 2.06 | 17.2 | 36.6 | 0.0 |
| linkfail | sdn | 4.25 | 35.0 | 68.1 | 0.0 |
| linkfail | baseline | 2.47 | 11355.0 | 13155.6 | 0.0 |

Scalability (one run per mode):

| Bots | sdn p50 / p95 (ms) | baseline p50 / p95 (ms) |
|---|---|---|
| 5 | 1.56 / 2.76 | 1.50 / 2.85 |
| 10 | 2.00 / 13.4 | 1.97 / 4.59 |
| 25 | 3.51 / 11.1 | 3.66 / 20.7 |
| 50 | 6.48 / 21.1 | 6.41 / 24.6 |
| 100 | 24.6 / 236.4 | 17.4 / 92.1 |

![Latency per scenario](../eval/results/latency_bar.png)
![Scalability](../eval/results/scalability.png)
![Link failure timeline](../eval/results/linkfail_timeline.png)
![Congestion CDF](../eval/results/congestion_cdf.png)

## What the results show

- **Idle:** the two modes are equal within run-to-run noise, so the controller adds no measurable cost when nothing is wrong.
- **Congestion:** a 12 Mbps UDP stream into the 10 Mbps bottleneck fills the link. In baseline every message queues behind it (median 165 ms). With SDN the chat traffic sits in its own queue and the heavy stream is limited by a meter, so the median stays at 3.5 ms: about 48 times lower at p50, 14 times at p95 and 9 times at p99.
- **Link failure:** the link s1-s2 goes down at 10 s and returns at 20 s. In baseline the switches keep sending into the dead link, so messages wait until it returns and the tail latency reaches about 11 s. With SDN the controller sees the port go down, deletes the routes and relearns them over the other path, so p95 stays at 35 ms. No messages are lost in either mode because TCP retransmits; the cost is delay.
- **Flood:** one spammer sending 200 messages per second on its own host. SDN did not improve latency here; baseline was slightly faster. A likely reason (not tested separately): spam messages are tiny, so their bandwidth is small next to the 10 Mbps bottleneck and the cost lands on the chat server's CPU, which throttling at the switch cannot relieve. The SDN run also pays a small cost to flag the user and install the throttle rules.
- **Scalability:** both modes behave the same up to 50 bots. At 100 bots SDN is worse at p95 (236 ms against 92 ms). With one run per mode on a VM this could be noise from CPU contention, and we have not repeated it, so we do not claim a trend.

## Limits

- All measurements are on a virtual machine, so absolute numbers depend on its CPU and timing; the comparison between modes is the useful result.
- Rules are per host IP, so a flag throttles every user on that host. The flood scenario therefore puts the spammer alone on h4.
- Scale runs have one run per mode, so there is no spread estimate for them.

## Reproduce

Run these from the repo root:

    sudo env PYTHONPATH=. python3 eval/run_experiments.py --core --runs 3
    sudo env PYTHONPATH=. python3 eval/run_experiments.py --only scale5,scale10,scale25,scale50,scale100 --runs 1
    make report
