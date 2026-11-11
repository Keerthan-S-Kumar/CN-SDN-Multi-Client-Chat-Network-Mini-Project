#!/usr/bin/env bash
# One-command helpers for the D2 demo (run from anywhere, as your normal user; it asks for sudo itself).
#
#   tools/demo.sh live    clean up, start the controller, open the triangle network.
#                         Then type ONE line at the mininet> prompt:  source tools/demo/all.cli
#   tools/demo.sh auto    fully automatic run of every demo check with PASS/FAIL (about 2 minutes)
#   tools/demo.sh stop    stop the controller and clean up Mininet
#
# Optional: RYU=/path/to/ryu-manager   MODE=baseline (controller mode for "live")
set -u
cd "$(dirname "$0")/.."
export PYTHONPATH=.
RYU="${RYU:-$HOME/ryu-env/bin/ryu-manager}"
PIDF=/tmp/chatsdn-ryu.pid

stop_all() {
  [ -f "$PIDF" ] && kill "$(cat "$PIDF")" 2>/dev/null; rm -f "$PIDF"
  sudo mn -c >/dev/null 2>&1
}

case "${1:-}" in
  live)
    [ -x "$RYU" ] || command -v "$RYU" >/dev/null || { echo "ryu-manager not found. Set RYU=/path/to/ryu-manager"; exit 1; }
    sudo -v || exit 1
    sudo mn -c >/dev/null 2>&1          # first: mn -c also kills ryu-manager
    rm -f eval/results/*.jsonl          # clears the old dashboard feed
    CONTROLLER_MODE="${MODE:-sdn}" nohup "$RYU" controller/app.py >/tmp/ryu.log 2>&1 &
    echo $! > "$PIDF"
    printf 'Starting the controller'
    for _ in $(seq 1 30); do
      curl -sf http://127.0.0.1:8080/health >/dev/null && break
      printf '.'; sleep 1
    done
    curl -sf http://127.0.0.1:8080/health >/dev/null || { echo; echo "Controller did not start. See /tmp/ryu.log"; stop_all; exit 1; }
    echo " up (log: /tmp/ryu.log)"
    echo "Dashboard: http://127.0.0.1:8080/ui"
    echo "At the mininet> prompt type:   source tools/demo/all.cli"
    echo "(or the steps one at a time from tools/demo/all.cli). Type exit to finish."
    trap stop_all EXIT
    sudo env PYTHONPATH=. python3 topology/topo.py --topo triangle --cli
    ;;
  auto)
    sudo -v || exit 1
    sudo mn -c >/dev/null 2>&1
    sudo env PYTHONPATH=. python3 tests/demo_auto.py --ryu "$RYU"
    ;;
  stop)
    stop_all; echo "stopped"
    ;;
  *)
    sed -n 2,10p "$0"; exit 2
    ;;
esac
