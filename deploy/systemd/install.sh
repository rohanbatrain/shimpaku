#!/usr/bin/env bash
# Install shimpakud as a user service (no sudo — run as the operator on the lab host).
# Idempotent: re-run after a `git pull` + `uv pip install -e .` to pick up new code.
set -euo pipefail
cd "$(dirname "$0")/../.."   # repo root

[ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" = "yes" ] || {
  echo "Linger is off for $USER — the service would stop at logout."
  echo "Fix: sudo loginctl enable-linger $USER   (see ops/phase0-root.sh)"
  exit 1
}

mkdir -p ~/.config/systemd/user ~/.config/shimpaku ~/.local/share/shimpaku
install -m 0644 deploy/systemd/shimpakud.service ~/.config/systemd/user/shimpakud.service
[ -f ~/.config/shimpaku/env ] || cat > ~/.config/shimpaku/env <<'EOF'
# shimpakud reads this at start (systemd EnvironmentFile); never logged, never committed.
# SHIMPAKU_LLM_BASE=http://127.0.0.1:4000
# SHIMPAKU_LLM_KEY=
EOF
chmod 600 ~/.config/shimpaku/env

systemctl --user daemon-reload
systemctl --user enable --now shimpakud.service
sleep 2
systemctl --user status shimpakud.service --no-pager -l | head -12
echo
echo "logs:    journalctl --user -u shimpakud -f"
echo "restart: systemctl --user restart shimpakud"
echo "stop:    systemctl --user stop shimpakud"
