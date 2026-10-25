#!/usr/bin/env bash
# Publish the read-only replay: snapshot the lab, render the landing page, ship to the VPS.
#   SHIMPAKU_LAB_HOST  ssh name of the lab host (runs shimpakud)
#   SHIMPAKU_VPS_HOST  ssh name of the VPS (Docker + compose installed, ports 80/443 open)
#   SHIMPAKU_DOMAINS   landing hosts, space-separated (e.g. shimpaku.example.in). For each <label>.<parent> the
#                      dashboard is <label>-app.<parent> and the status page <label>-status.<parent>: one level
#                      under the zone, so Cloudflare's free Universal SSL (*.<parent>) covers every name.
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
sib() { local d=$1; printf "%s-%s.%s" "${d%%.*}" "$2" "${d#*.}"; }   # shimpaku.x.in app -> shimpaku-app.x.in
APP_HOSTS=$(for d in $DOMAINS; do printf "%s, " "$(sib $d app)"; done); APP_HOSTS=${APP_HOSTS%, }
STATUS_HOSTS=$(for d in $DOMAINS; do printf "%s, " "$(sib $d status)"; done); STATUS_HOSTS=${STATUS_HOSTS%, }
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
  # Read the daemon where it listens (its NetBird address once public); reads are a public visitor's.
  ssh "$LAB" "cd ~/src/shimpaku && .venv/bin/shimpaku snapshot export --out /tmp/shimpaku-snapshot.json \
      --base http://$LAB_UP --redact \"\$(uname -n)\" --redact \"\$USER\""
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
# the one-liner (`curl -fsSL https://<landing>/install.sh | bash`): deploy/own/get.sh, pinned to the commit the
# public repository's main has right now, so it downloads exactly what is published
if [[ -f deploy/own/get.sh ]]; then
  PIN=$(git ls-remote "$REPO" refs/heads/main | cut -f1)
  [[ "$PIN" =~ ^[0-9a-f]{40}$ ]] || { echo "cannot read the public repository's main commit from $REPO" >&2; exit 1; }
  sed -e "s#__SHIMPAKU_PIN__#$PIN#g" deploy/own/get.sh > "$HERE/site-built/install.sh"
  echo "install.sh pinned to $PIN"
fi

echo "== ship"
ssh "$VPS" 'mkdir -p ~/shimpaku'
git archive --format=tar HEAD | ssh "$VPS" 'cd ~/shimpaku && tar -xf -'
# the containers run as unprivileged users; the snapshot and the site are public data
chmod -R a+rX "$HERE/snapshot" "$HERE/site-built"
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
APP="https://$(sib $DOMAIN app)"
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
# Virga live: the anonymous health read that Uptime Kuma's "Live lab" monitors use. Soft check: the lab
# daemon may not run the Virga build yet (ops/golive/run.sh lab-config), so a miss warns, never fails.
if h=$(curl -fsS "$APP/api/public/health" 2>/dev/null | python3 -c 'import json,sys; h=json.load(sys.stdin); print(" ".join(f"{k}={h[k]}" for k in ("campus", "fabric", "drill", "lab")))' 2>/dev/null); then
  echo "api/public/health: $h"
else
  echo "note: /api/public/health is not answering yet (the lab daemon needs the Virga live build); the Live lab monitors stay red until it does" >&2
fi
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
