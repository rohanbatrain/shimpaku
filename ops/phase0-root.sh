#!/usr/bin/env bash
# Phase 0 root steps for the lab host. Run once as root:  sudo bash ops/phase0-root.sh <operator-user>
# Idempotent: every step checks before it changes anything, and the script ends with a verification.
set -euo pipefail

OP="${1:?usage: sudo bash ops/phase0-root.sh <operator-user>}"
[ "$(id -u)" = 0 ] || { echo "run as root (sudo)"; exit 1; }
id "$OP" >/dev/null

step() { printf '\n== %s\n' "$*"; }

step "1. linger for $OP — user services (dashboard, MCP server) survive logout"
loginctl enable-linger "$OP"

step "2. lab bridges forward 802.1X (EAPOL, 01:80:c2:00:00:03) and LLDP (…:0e) — group_fwd_mask 0x4008"
for b in /sys/class/net/lab-*/bridge/group_fwd_mask; do
  [ -e "$b" ] || continue
  echo 0x4008 > "$b"
  echo "  $(basename "$(dirname "$(dirname "$b")")") -> $(cat "$b")"
done
echo "  (runtime only; lab-gen.py's net-up script sets it on every 'make up')"

step "3. users and the shared lock directory"
getent group shimpaku >/dev/null || groupadd --system shimpaku
id aiops-bench >/dev/null 2>&1 || useradd --system --create-home --home-dir /home/aiops-bench --shell /usr/bin/bash aiops-bench
id shim-agent  >/dev/null 2>&1 || useradd --system --create-home --home-dir /home/shim-agent  --shell /usr/bin/bash shim-agent
for u in "$OP" aiops-bench shim-agent; do usermod -aG shimpaku "$u"; done
install -d -o "$OP" -g shimpaku -m 2770 /home/lab/shimpaku          # locks.db: every writer can open it
install -d -o aiops-bench -g aiops-bench -m 0700 /home/aiops-bench/store   # ground truth: bench user only
chmod 0700 /home/aiops-bench /home/shim-agent

step "4. sandbox for external agents (user shim-agent): no direct path to the lab devices"
# shim-agent may reach the internet (its model API) and the MCP endpoint on this host,
# but never the lab management network — every device action has to go through MCP.
nft list table inet shimpaku_sandbox >/dev/null 2>&1 && nft delete table inet shimpaku_sandbox
nft add table inet shimpaku_sandbox
nft add chain inet shimpaku_sandbox out '{ type filter hook output priority filter; policy accept; }'
nft add rule inet shimpaku_sandbox out meta skuid shim-agent ip daddr 10.99.0.0/24 counter reject
echo "  (runtime only; re-run this script after a reboot, like lab-net-up.sh)"

step "verify"
printf '  linger:          %s\n' "$(loginctl show-user "$OP" -p Linger --value)"
printf '  bridge masks:    %s\n' "$(cat /sys/class/net/lab-*/bridge/group_fwd_mask | sort -u | tr '\n' ' ')"
printf '  groups of %s:   %s\n' "$OP" "$(id -nG "$OP")"
printf '  lock dir:        %s\n' "$(stat -c '%A %U:%G' /home/lab/shimpaku)"
printf '  bench store:     %s\n' "$(stat -c '%A %U:%G' /home/aiops-bench/store)"
printf '  shim-agent → lab: '
if sudo -u shim-agent timeout 3 bash -c 'exec 3<>/dev/tcp/10.99.0.11/22' 2>/dev/null; then echo "OPEN (wrong)"; else echo "blocked (correct)"; fi
printf '  shim-agent → bench store: '
if sudo -u shim-agent ls /home/aiops-bench/store >/dev/null 2>&1; then echo "READABLE (wrong)"; else echo "denied (correct)"; fi
echo
echo "done. log out and back in (or 'newgrp shimpaku') so $OP picks up the shimpaku group."
