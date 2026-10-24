#!/usr/bin/env bash
# Publish the read-only replay: snapshot the lab, render the landing page, ship to the VPS.
#   SHIMPAKU_LAB_HOST  ssh name of the lab host (runs shimpakud)
#   SHIMPAKU_VPS_HOST  ssh name of the VPS (Docker + compose installed, ports 80/443 open)
#   SHIMPAKU_DOMAIN    landing page domain; the dashboard is app.<domain>
#   SHIMPAKU_REPO_URL  public repository URL linked from the landing page
# Usage: deploy/vps/deploy.sh [--no-snapshot]   (reuse the last snapshot)
set -euo pipefail
LAB="${SHIMPAKU_LAB_HOST:?set SHIMPAKU_LAB_HOST}"
VPS="${SHIMPAKU_VPS_HOST:?set SHIMPAKU_VPS_HOST}"
DOMAIN="${SHIMPAKU_DOMAIN:?set SHIMPAKU_DOMAIN}"
REPO="${SHIMPAKU_REPO_URL:?set SHIMPAKU_REPO_URL}"
cd "$(dirname "$0")/../.."
HERE=deploy/vps
mkdir -p "$HERE/snapshot" "$HERE/site-built"

if [[ "${1:-}" != "--no-snapshot" ]]; then
  echo "== snapshot on the lab host"
  # Redact the lab host's names and the service user; the export refuses to write if any survive.
  ssh "$LAB" 'cd ~/src/shimpaku && .venv/bin/shimpaku snapshot export --out /tmp/shimpaku-snapshot.json \
      --redact "$(uname -n)" --redact "$USER"'
  scp -q "$LAB:/tmp/shimpaku-snapshot.json" "$HERE/snapshot/snapshot.json"
  ssh "$LAB" 'rm -f /tmp/shimpaku-snapshot.json'
fi
[[ -s "$HERE/snapshot/snapshot.json" ]] || { echo "no snapshot; run without --no-snapshot" >&2; exit 1; }
# Our own names must not appear either (the lab host's ssh alias, the VPS's).
for t in "$LAB" "$VPS"; do
  if grep -qiF -- "$t" "$HERE/snapshot/snapshot.json"; then echo "snapshot mentions $t; refusing" >&2; exit 1; fi
done

echo "== landing page"
sed -e "s#__DOMAIN__#$DOMAIN#g" -e "s#__REPO__#$REPO#g" "$HERE/site/index.html" > "$HERE/site-built/index.html"

echo "== ship"
ssh "$VPS" 'mkdir -p ~/shimpaku'
git archive --format=tar HEAD | ssh "$VPS" 'cd ~/shimpaku && tar -xf -'
tar -C "$HERE" -cf - snapshot site-built | ssh "$VPS" 'cd ~/shimpaku/deploy/vps && tar -xf -'
ssh "$VPS" "cd ~/shimpaku/deploy/vps && printf 'SHIMPAKU_DOMAIN=%s\n' '$DOMAIN' > .env \
  && docker compose up -d --build && docker compose restart replay"

echo "== check"
sleep 5
curl -fsS "https://app.$DOMAIN/api/whoami" | python3 -c 'import json,sys; w=json.load(sys.stdin); assert w["replay"] and not w["can_approve"], w; print("replay ok, read-only")'
curl -fsS -o /dev/null -w "landing %{http_code}\n" "https://$DOMAIN/"
code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "https://app.$DOMAIN/mcp")
[[ "$code" == 404 ]] && echo "mcp closed (404)" || { echo "mcp answered $code" >&2; exit 1; }
