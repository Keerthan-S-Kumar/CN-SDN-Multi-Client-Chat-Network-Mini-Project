#!/usr/bin/env bash
# Run INSIDE the Ubuntu 22.04 VM:  bash tools/setup_vm.sh
# Installs Mininet, Open vSwitch, iperf3, tshark and Ryu, then verifies each. Safe to re-run.
set -uo pipefail
export DEBIAN_FRONTEND=noninteractive
ok()   { printf '  [ok]   %s\n' "$1"; }
fail() { printf '  [FAIL] %s\n' "$1"; FAILED=1; }
FAILED=0

echo "== 1/4 apt packages"
sudo apt-get update -y >/dev/null
sudo apt-get install -y git curl build-essential python3-pip python3-venv python3-dev \
     mininet openvswitch-switch iperf3 tshark net-tools tmux >/dev/null || fail "apt install"
sudo systemctl enable --now openvswitch-switch >/dev/null 2>&1 || sudo service openvswitch-switch start

echo "== 2/4 Ryu (apt first, pinned venv as fallback)"
ryu_ok() { "$1" --version >/dev/null 2>&1; }
RYU=""
if sudo apt-get install -y python3-ryu >/dev/null 2>&1 && ryu_ok ryu-manager; then
  RYU=ryu-manager
else
  python3 -m venv "$HOME/ryuenv"
  "$HOME/ryuenv/bin/pip" install -q "setuptools==57.5.0" wheel
  "$HOME/ryuenv/bin/pip" install -q "eventlet==0.30.2"
  "$HOME/ryuenv/bin/pip" install -q ryu --no-build-isolation
  if ! ryu_ok "$HOME/ryuenv/bin/ryu-manager"; then
    "$HOME/ryuenv/bin/pip" install -q "dnspython<2"            # known eventlet 0.30 / dnspython 2 clash
  fi
  ryu_ok "$HOME/ryuenv/bin/ryu-manager" && RYU="$HOME/ryuenv/bin/ryu-manager"
fi

echo "== 3/4 versions"
[ -n "$RYU" ] && ok "ryu-manager $($RYU --version 2>&1 | tail -1)  ($RYU)" || fail "Ryu not working - paste the error"
sudo ovs-vsctl --version | head -1 | sed 's/^/  [ok]   /' || fail "open vswitch"
sudo mn --version 2>&1 | sed 's/^/  [ok]   mininet /' || fail "mininet"
iperf3 --version | head -1 | sed 's/^/  [ok]   /'
python3 --version | sed 's/^/  [ok]   /'
python3 -c "import mininet.net" 2>/dev/null && ok "python3 can import mininet" || fail "python mininet module"

echo "== 4/4 Mininet smoke test (2 hosts, pingall)"
sudo mn -c >/dev/null 2>&1
if sudo mn --test pingall 2>&1 | grep -q "0% dropped"; then ok "pingall 0% dropped"; else fail "mininet pingall"; fi
sudo mn -c >/dev/null 2>&1

echo
if [ "$FAILED" = 0 ]; then echo "ALL GOOD. Next: make test && make controller"; else echo "Some checks failed - paste the output above."; fi
