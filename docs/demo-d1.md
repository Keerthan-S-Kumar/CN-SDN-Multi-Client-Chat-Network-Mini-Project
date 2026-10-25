# D1 live demo script (about 5 minutes)

Structure by Harsha; client commands corrected to match `chat/client.py` on `main` (it takes `--user`, which registers the
name if it is new, and reads commands from stdin; there are no `--register` or `--linger` flags).

Start fresh: close old windows, then `sudo mn -c` BEFORE starting anything (it kills Ryu if run later).
Take a screenshot at every step marked **[shot]**; they are the evidence for "initial testing and demo".

1. Terminal 1, controller:
   ```
   cd ~/CN-SDN-Multi-Client-Chat-Network-Mini-Project && source ~/ryu-env/bin/activate
   PYTHONPATH=. CONTROLLER_MODE=sdn ryu-manager controller/app.py
   ```
   Say: Ryu app; the switch connects over OpenFlow 1.3 and gets its default rules. **[shot]** log line `switch 1 connected`.
2. Terminal 2, topology (run `sudo -v` first):
   ```
   sudo env PYTHONPATH=. python3 topology/topo.py --topo single --cli
   mininet> pingall           -> 0% dropped
   ```
   Say: h1 = chat server, h2-h4 = clients, one switch. **[shot]**
3. Terminal 3, the static rules:
   ```
   sudo ovs-ofctl -O OpenFlow13 dump-flows s1 table=0 | grep tp_dst=500
   ```
   Say: ports 5001-5003 (chat) are dropped for unregistered hosts (priority 100); port 5000 (control) is open (priority 150). **[shot]**
4. Start the server on h1:
   ```
   mininet> h1 PYTHONPATH=. python3 chat/server.py --host 10.0.0.1 &
   ```
5. Gating test, blocked:
   ```
   mininet> h2 python3 -c "import socket; socket.create_connection(('10.0.0.1',5001),timeout=3); print('connected')"
   ```
   Expected: `TimeoutError`. Say: an unregistered host cannot reach the chat port; the switch drops it. **[shot]**
   Optional: `mininet> h2 python3 -c "import socket; socket.create_connection(('10.0.0.1',5000),timeout=3); print('control port open')"`
6. Register, rules appear:
   ```
   mininet> h2 sh -c "(printf '/join lobby\n'; sleep 120) | PYTHONPATH=. python3 chat/client.py --server 10.0.0.1 --user bob" > /tmp/bob.out 2>&1 &
   ```
   Terminal 3:
   ```
   sudo ovs-ofctl -O OpenFlow13 dump-flows s1 table=0 | grep 10.0.0.2
   curl -s localhost:8080/state
   ```
   Say: the server told the controller (`POST /event`); the controller added allow rules at priority 200 with queues:
   5001 to queue 2, 5002 to queue 1 (high), 5003 to queue 3 (bulk). **[shot]** the 3 rules for 10.0.0.2 and `/state`.
7. Chat between two hosts:
   ```
   mininet> h3 sh -c "(printf '/join lobby\n'; sleep 20) | PYTHONPATH=. python3 chat/client.py --server 10.0.0.1 --user erin" > /tmp/erin.out 2>&1 &
   mininet> h4 sh -c "(sleep 3; printf '/join lobby\n/msg lobby hello from h4\n'; sleep 2) | PYTHONPATH=. python3 chat/client.py --server 10.0.0.1 --user frank"
   mininet> h3 cat /tmp/erin.out
   ```
   Expected: erin's output shows `[lobby] frank: hello from h4`. **[shot]**
   Private message (optional): add `/pm erin hi` to frank's input.
8. Disconnect, rules removed: wait about 20 s (erin's client ends), repeat the grep for 10.0.0.3 in terminal 3.
   Expected: no rules for 10.0.0.3; bob (10.0.0.2) stays registered until his 120 s sleep ends. **[shot]** after ~2 minutes:
   `dump-flows ... | grep tp_dst=500` shows only the 4 static rules again.
9. Topology and tests: the triangle (s1, s2, s3) is in `docs/architecture.md`; tests are in `tests/` and `controller/test_logic.py`.

If something breaks: Ctrl-C everything, `sudo mn -c`, restart from step 1 in order (Ryu first, then topology).
