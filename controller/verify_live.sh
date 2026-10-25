#!/usr/bin/env bash
# OWNER: B. Live check of the controller against a running Mininet. Run on the VM from the repo root:
#   terminal 1: make controller            terminal 2: make topo TOPO=single   (or triangle)
#   terminal 3: bash controller/verify_live.sh
# Posts fake chat events and checks the OpenFlow rules that appear/disappear on every switch.
# Uses 10.0.0.2 / 10.0.0.3 / 10.0.0.4; leaves the controller clean at the end.
export CONTROLLER_URL=${CONTROLLER_URL:-http://127.0.0.1:8080}
export PYTHONPATH=.
PASS=0; FAIL=0
BRIDGES=$(sudo ovs-vsctl list-br 2>/dev/null | sort)
[ -n "$BRIDGES" ] || { echo "no OVS bridges found - is Mininet running?"; exit 2; }
curl -fs "$CONTROLLER_URL/health" >/dev/null || { echo "controller REST not reachable at $CONTROLLER_URL"; exit 2; }

flows() { sudo ovs-ofctl -O OpenFlow13 dump-flows "$1" table=0 2>/dev/null; }
count() { flows "$1" | grep -c "priority=$2,.*nw_src=$3"; }          # rules of prio $2 for source ip $3
ev()    { python3 tools/post_event.py "$@" >/dev/null; sleep 0.4; }

expect() {  # expect <prio> <ip> <want-count-per-switch> <label>
  local ok=1 b n
  for b in $BRIDGES; do
    n=$(count "$b" "$1" "$2")
    [ "$n" = "$3" ] || { ok=0; echo "    $b: found $n, wanted $3"; flows "$b" | grep "nw_src=$2" | sed 's/^/      /'; }
  done
  if [ $ok = 1 ]; then PASS=$((PASS+1)); echo "  PASS  $4"; else FAIL=$((FAIL+1)); echo "  FAIL  $4"; fi
}
expect_static() {  # static drop/control rules present on each switch
  local ok=1 b
  for b in $BRIDGES; do
    d=$(flows "$b" | grep -c "priority=100,tcp,tp_dst=500[123] actions=drop")
    c=$(flows "$b" | grep -c "priority=150,tcp,tp_dst=5000 ")
    [ "$d" = 3 ] && [ "$c" = 1 ] || { ok=0; echo "    $b: drop rules=$d (want 3), control rules=$c (want 1)"; }
  done
  if [ $ok = 1 ]; then PASS=$((PASS+1)); echo "  PASS  $1"; else FAIL=$((FAIL+1)); echo "  FAIL  $1"; fi
}

echo "switches: $(echo $BRIDGES | tr '\n' ' ')"
for u in tester:10.0.0.2 bob:10.0.0.3; do ev disconnect ${u%%:*} ${u##*:}; done   # start clean
ev unflag tester 10.0.0.2

expect_static "static gate: 3 drop rules (5001-5003) + control rule (5000)"
expect 200 10.0.0.2 0 "unregistered host has no allow rules"
ev register tester 10.0.0.2
expect 200 10.0.0.2 3 "register -> 3 allow rules on every switch"
ev register bob 10.0.0.3
ev join bob 10.0.0.3 --room vip
expect 250 10.0.0.3 1 "joining policy.json vip room -> VIP rule"
ev leave bob 10.0.0.3 --room vip
expect 250 10.0.0.3 0 "leaving the vip room -> VIP rule removed"
ev flag tester 10.0.0.2 --reason spam
expect 300 10.0.0.2 3 "flag -> 3 throttle rules"
ev unflag tester 10.0.0.2 --reason spam
expect 300 10.0.0.2 0 "unflag -> throttle rules removed"
ev block "" 10.0.0.4 --reason bad_login
expect 400 10.0.0.4 4 "block -> 4 drop rules (ports 5000-5003)"
ev disconnect tester 10.0.0.2
expect 200 10.0.0.2 0 "disconnect -> allow rules removed"
expect_static "drop rules survived the deletes"
ev disconnect bob 10.0.0.3

M=$(curl -fs "$CONTROLLER_URL/metrics")
echo "$M" | python3 -c 'import json,sys; m=json.load(sys.stdin); assert m["mode"] in ("sdn","baseline"); print("  PASS  /metrics:", {k:m[k] for k in ("mode","congested","blocked","links_down")})' \
  && PASS=$((PASS+1)) || { FAIL=$((FAIL+1)); echo "  FAIL  /metrics not valid JSON"; }

echo; echo "block on 10.0.0.4 expires by itself after 60 s (BLOCK_SECONDS); wait and re-run 'make flows' to see it go."
echo "RESULT: $PASS passed, $FAIL failed"; [ "$FAIL" = 0 ]
