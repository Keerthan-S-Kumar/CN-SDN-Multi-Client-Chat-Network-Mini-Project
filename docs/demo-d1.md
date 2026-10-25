# D1 live demo script (about 5 minutes)

Start fresh: close old windows, then `sudo mn -c` BEFORE starting anything (it kills Ryu if run later).

1. Terminal 1, controller:
   cd ~/CN-SDN-Multi-Client-Chat-Network-Mini-Project && source ~/ryu-env/bin/activate
   PYTHONPATH=. CONTROLLER_MODE=sdn ryu-manager controller/app.py
   Say: Ryu app; switch connects over OpenFlow 1.3 and gets default rules.
2. Terminal 2, topology (run `sudo -v` first):
   sudo env PYTHONPATH=. python3 topology/topo.py --topo single --cli
   mininet> pingall           -> 0% dropped. Say: h1 = chat server, h2-h4 = clients.
3. Terminal 3, show the default rules:
   sudo ovs-ofctl -O OpenFlow13 dump-flows s1 | grep tp_dst=500
   Say: ports 5001-5003 (chat) are dropped for unregistered hosts; port 5000 (control) is open.
4. mininet> h1 PYTHONPATH=. python3 chat/server.py --host 10.0.0.1 &
5. Gating test (blocked):
   mininet> h2 python3 -c "import socket; socket.create_connection(('10.0.0.1',5001),timeout=3); print('connected')"
   -> TimeoutError. Say: unregistered host cannot reach the chat port; the switch drops it.
6. Register, rules appear:
   mininet> h2 python3 chat/client.py --server 10.0.0.1 --user bob --register --linger 120 < /dev/null &
   Terminal 3: sudo ovs-ofctl -O OpenFlow13 dump-flows s1 | grep 10.0.0.2
   Say: server told the controller (REST POST /event); controller added allow rules priority 200
   with queues: 5001 -> queue 2, 5002 -> queue 1 (high), 5003 -> queue 3 (bulk).
7. Chat between hosts:
   mininet> h3 sh -c "printf '/join lobby\n' | python3 chat/client.py --server 10.0.0.1 --user erin --register --linger 20" &
   mininet> h4 sh -c "(sleep 3; printf '/join lobby\n/msg lobby hello from h4\n') | python3 chat/client.py --server 10.0.0.1 --user frank --register --linger 3"
   Press Enter. erin's output shows "[lobby] frank: hello from h4".
8. Disconnect, rules removed: wait about 20 s, repeat the grep in terminal 3 -> only the 4 default rules.
9. Topology: triangle (s1,s2,s3) is in docs/architecture.md; tests: tests/ folder.

If something breaks: Ctrl-C everything, `sudo mn -c`, restart from step 1 in order (Ryu first, then topology).
