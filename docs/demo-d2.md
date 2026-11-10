# D2 live demo script (about 8 minutes)

Setup, same order as D1 (cleanup, then Ryu, then topology):
- T3: `sudo -v`, then `sudo mn -c`
- T1: `cd ~/CN-SDN-Multi-Client-Chat-Network-Mini-Project`, then
  `PYTHONPATH=. CONTROLLER_MODE=sdn ~/ryu-env/bin/ryu-manager controller/app.py`
- T2 (run `sudo -v` first): `cd ~/CN-SDN-Multi-Client-Chat-Network-Mini-Project`, then
  `sudo env PYTHONPATH=. python3 topology/topo.py --topo triangle --cli`
  The `sch_htb ... quantum is big` lines are harmless.
Open `eval/results/latency_bar.png`, `scalability.png`, `linkfail_timeline.png` and `congestion_cdf.png` in a file viewer before you start.

## A. Results first (1.5 min)
Show `latency_bar.png` and the table in `docs/results.md`.
Say: "Same triangle topology and same queues, only the controller mode changes. With congestion the median chat latency drops from 165 ms to 3.5 ms. With a link failure the p95 drops from 11 seconds to 35 ms. When idle there is no difference, so the controller costs nothing. Flood showed no gain, and at 100 bots both modes are overloaded; both are in the results doc."

## B. Live link failure and reroute (3 min)
At `mininet>` (type without the prompt text):

    pingall
    h2 ping -c 100 -i 0.2 10.0.0.3 > /tmp/ping.txt &

T3, the route before the failure:

    sudo ovs-ofctl -O OpenFlow13 dump-flows s2 table=1 | grep 00:00:00:00:00:03

Expect `actions=output:3` (port 3, the direct link to s3). Now at `mininet>`:

    link s2 s3 down

T3, the same command again. Expect `actions=output:2` (port 2, the detour via s1). T1 shows the controller's link-down handling.
At `mininet>`, bring it back after about 5 seconds:

    link s2 s3 up

Wait for the ping to finish (about 20 seconds), then:

    sh tail -3 /tmp/ping.txt

Say: "The controller saw the port go down, deleted the routes, and relearned them over s1. In the rehearsal no pings were lost; traffic carried on."
(To compare with no SDN, the baseline link-failure numbers are in the results: p95 11 seconds.)

## C. Congestion with queues and meters (2.5 min)
At `mininet>`:

    h1 PYTHONPATH=. python3 chat/server.py --host 10.0.0.1 > /tmp/server.log 2>&1 &
    h1 iperf -s -u -p 5201 > /dev/null 2>&1 &
    h3 iperf -c 10.0.0.1 -u -b 12M -t 25 -p 5201 > /dev/null 2>&1 &
    h2 python3 chat/bot.py --users 3 --prefix demo --duration 10 --out /tmp/demo.csv

The last command prints a line `latency ms: p50=... p95=...`. Expect single-digit milliseconds even though a 12 Mbps stream is filling the 10 Mbps link. In queue-stats, queue 0 carries the iperf stream and queues 1 and 2 carry chat (control and room).
T3 (while iperf is running):

    sudo ovs-ofctl -O OpenFlow13 dump-meters s1
    sudo ovs-ofctl -O OpenFlow13 queue-stats s1

Say: "Chat traffic sits in its own priority queue and the heavy stream is limited by a meter. In the baseline run of the same scenario the median was 165 ms."

## D. Block rules (1 min)
T3:

    PYTHONPATH=. python3 tools/post_event.py block "" 10.0.0.4 --reason bad_login
    sudo ovs-ofctl -O OpenFlow13 dump-flows s1 table=0 | grep priority=400

Expect 4 rules at priority 400 (ports 5000 to 5003). Say: "This is what the server sends after repeated bad logins; the block lifts by itself after 60 seconds."

## E. File sharing under load (1.5 min)
With the iperf stream from C still running (or start it again), at `mininet>`:

    h2 PYTHONPATH=. python3 eval/file_transfer.py --server 10.0.0.1 --mb 3

It prints the upload and download time of a 3 MB file and checks the bytes are identical. Say: "Files travel on port 5003, so the switch puts them in the bulk queue. They are slower than chat, but the chat latency in the last step did not move." Open the dashboard Traffic tab: the Bulk bar for h2 grows by about 3 MB. In the web chat, drag a file into a room, and the other user gets a Download card.

## Closing line
"The controller adds no cost when idle and helps most when the network is under stress: congestion and link failure. Its limits are the flood scenario, where throttling at the switch does not help a loaded server, and the rules being per host."

If something fails: Ctrl-C T1 and T2, `sudo mn -c` in T3, restart from Setup; or show the saved graphs and run `python3 eval/analyze.py`.
