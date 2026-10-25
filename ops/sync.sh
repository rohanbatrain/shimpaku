#!/usr/bin/env bash
# Mirror the working tree to the lab host (~/src/shimpaku). The host has no rsync, so tar over ssh.
# Keeps the host's .venv, data and web/node_modules; never sends .local/ (operator secrets and logs).
set -euo pipefail
HOST="${SHIMPAKU_LAB_HOST:?set SHIMPAKU_LAB_HOST to the lab host ssh name}"
cd "$(dirname "$0")/.."
COPYFILE_DISABLE=1 tar czf - --exclude .venv --exclude .git --exclude web/node_modules --exclude __pycache__ \
  --exclude '.pytest_cache' --exclude .local --exclude '.DS_Store' . | ssh -o BatchMode=yes "$HOST" 'mkdir -p ~/src/shimpaku && tar xzf - -C ~/src/shimpaku 2>/dev/null'
echo "synced to $HOST:~/src/shimpaku"
