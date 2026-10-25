#!/usr/bin/env bash
# Publish the read-only replay: snapshot the lab, render the landing page, ship to the VPS.
#   SHIMPAKU_LAB_HOST  ssh name of the lab host (runs shimpakud)
#   SHIMPAKU_VPS_HOST  ssh name of the VPS (Docker + compose installed, ports 80/443 open)
#   SHIMPAKU_DOMAINS   landing domains, space-separated; each serves the landing and app.<domain> the dashboard
#   SHIMPAKU_REPO_URL  public repository URL linked from the landing page
#   SHIMPAKU_LAB_UPSTREAM  host:port of shimpakud on the lab's NetBird address (the edge's live upstream)
#   SHIMPAKU_VPS_SUDO_SECRETS  optional: SOPS file holding ansible_become_password for sudo on the VPS
# Usage: deploy/vps/deploy.sh [--no-snapshot]   (reuse the last snapshot)
set -euo pipefail
LAB="${SHIMPAKU_LAB_HOST:?set SHIMPAKU_LAB_HOST}"
VPS="${SHIMPAKU_VPS_HOST:?set SHIMPAKU_VPS_HOST}"
DOMAINS="${SHIMPAKU_DOMAINS:?set SHIMPAKU_DOMAINS}"
DOMAIN="${DOMAINS%% *}"   # checks run against the first
SITE_HOSTS=$(printf "%s, " $DOMAINS); SITE_HOSTS=${SITE_HOSTS%, }
APP_HOSTS=$(printf "app.%s, " $DOMAINS); APP_HOSTS=${APP_HOSTS%, }
STATUS_HOSTS=$(printf "status.%s, " $DOMAINS); STATUS_HOSTS=${STATUS_HOSTS%, }
CORS=$(printf "https://%s," $DOMAINS); CORS=${CORS%,}
REPO="${SHIMPAKU_REPO_URL:?set SHIMPAKU_REPO_URL}"
LAB_UP="${SHIMPAKU_LAB_UPSTREAM:?set SHIMPAKU_LAB_UPSTREAM (lab NetBird host:port)}"
[[ "$LAB_UP" =~ ^[^:/[:space:]]+:[0-9]+$ ]] || { echo "SHIMPAKU_LAB_UPSTREAM must be host:port" >&2; exit 1; }
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
cp -R "$HERE/site/." "$HERE/site-built/"   # images and any other static files
sed -e "s#__REPO__#$REPO#g" "$HERE/site/index.html" > "$HERE/site-built/index.html"

echo "== ship"
ssh "$VPS" 'mkdir -p ~/shimpaku'
git archive --format=tar HEAD | ssh "$VPS" 'cd ~/shimpaku && tar -xf -'
tar -C "$HERE" -cf - snapshot site-built | ssh "$VPS" 'cd ~/shimpaku/deploy/vps && tar -xf -'
ssh "$VPS" "cd ~/shimpaku/deploy/vps && printf 'SHIMPAKU_SITE_HOSTS=%s\nSHIMPAKU_APP_HOSTS=%s\nSHIMPAKU_STATUS_HOSTS=%s\nSHIMPAKU_PUBLIC_CORS_ORIGINS=%s\nSHIMPAKU_LAB_UPSTREAM=%s\n' '$SITE_HOSTS' '$APP_HOSTS' '$STATUS_HOSTS' '$CORS' '$LAB_UP' > .env"
# Docker needs root on the VPS (no docker group: it is root-equivalent). The sudo password is decrypted
# on this machine and piped to `sudo -S` on stdin, so it is never on a command line or in a log (RUN-0004).
vps_sudo() {
  if [[ -n "${SHIMPAKU_VPS_SUDO_SECRETS:-}" ]]; then
    sops -d --extract '["ansible_become_password"]' "$SHIMPAKU_VPS_SUDO_SECRETS" | ssh "$VPS" "sudo -S -p '' sh -c $(printf %q "$1")"
  else ssh "$VPS" "$1"; fi   # e.g. the login user is already in the docker group
}
vps_sudo "cd ~$(ssh "$VPS" whoami)/shimpaku/deploy/vps && docker compose up -d --build && docker compose restart replay"

echo "== check"
sleep 15   # first active health check of the lab upstream
APP="https://app.$DOMAIN"
code() { curl -s -o /dev/null -w "%{http_code}" "$@"; }
[[ "$(code "https://$DOMAIN/")" == 200 ]] && echo "landing 200" || { echo "landing not 200" >&2; exit 1; }
[[ "$(code "$APP/")" == 200 ]] && echo "app 200" || { echo "app shell not 200" >&2; exit 1; }
[[ "$(code "$APP/start")" == 200 ]] && echo "/start (SPA route) 200" || { echo "/start not 200" >&2; exit 1; }
mode=$(curl -fsS "$APP/api/whoami" | python3 -c 'import json,sys; w=json.load(sys.stdin)
if w.get("replay"):
    assert not w["can_approve"], w; print("replay")
else:
    print("live")')
echo "api/whoami: $mode"
mcp=$(code -X POST "$APP/mcp")
wbody=$(mktemp)
write=$(curl -s -o "$wbody" -w "%{http_code}" -X POST "$APP/api/actions/x/approve")
if [[ "$mode" == live ]]; then
  [[ "$mcp" == 401 ]] && echo "mcp 401 without token" || { echo "mcp answered $mcp, want 401" >&2; exit 1; }
  [[ "$write" =~ ^40[134]$ ]] && echo "anonymous write refused ($write)" || { echo "anonymous write answered $write" >&2; exit 1; }
else
  [[ "$mcp" == 503 ]] && echo "mcp 503 (lab down, never replayed)" || { echo "mcp answered $mcp, want 503" >&2; exit 1; }
  [[ "$write" == 503 ]] && grep -q "recorded replay" "$wbody" && echo "write 503 JSON (lab down)" \
    || { echo "write answered $write while lab down" >&2; exit 1; }
  echo "note: lab is down or unreachable over NetBird; reads are served by the replay"
fi
rm -f "$wbody"
