#!/usr/bin/env bash
# Take Shimpaku public, step by step. Every step is idempotent and safe to re-run.
#
#   ops/golive/run.sh check          what is ready and what is missing (changes nothing)
#   ops/golive/run.sh vps-key        install your agent's public key on the VPS (asks the root password once)
#   ops/golive/run.sh lab-secrets    on the lab host: set the operator password, enable TOTP
#   ops/golive/run.sh lab-config     on the lab host: sign-in on, public view, NetBird bind, trusted edge, Virga live drills
#   ops/golive/run.sh deploy         snapshot → landing page → ship the edge to the VPS → checks
#   ops/golive/run.sh kuma           Uptime Kuma: monitors + the public status page (Website, Live lab) via its admin URL
#   ops/golive/run.sh verify         the public checks only
#
# Settings: .local/settings.env (copy ops/golive/settings.example.env). Logs: .local/golive.log.
# `kuma` also needs KUMA_CREDS (default .local/kuma-admin.txt, git-ignored, mode 600): `username: …` and
# `password: …` lines, or the bare user on line 1 and password on line 2. They are read into the
# environment of one node process; nothing prints them.
# Out of scope here (ADR-0056: hosts are Ansible): the VPS baseline (users, firewall, NetBird, Docker),
# which lives in infra-substrate. `check` tells you if it is missing.
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT=$PWD
LOCAL="$ROOT/.local"
umask 077
mkdir -p "$LOCAL"
SETTINGS="$LOCAL/settings.env"
[[ -f "$SETTINGS" ]] || { cp ops/golive/settings.example.env "$SETTINGS"; echo "created $SETTINGS — fill it in, then re-run" >&2; exit 1; }
# shellcheck disable=SC1090
set -a; . "$SETTINGS"; set +a
LOG="$LOCAL/golive.log"
sib() { local d=$1; printf "%s-%s.%s" "${d%%.*}" "$2" "${d#*.}"; }   # one level under the zone (free Cloudflare SSL)
log() { printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG"; }
need() { local v; for v in "$@"; do [[ -n "${!v:-}" && "${!v}" != *"<"* ]] || { echo "set $v in $SETTINGS" >&2; exit 1; }; done; }
ok() { printf '  \033[32mok\033[0m   %s\n' "$*"; }
no() { printf '  \033[31mno\033[0m   %s\n' "$*"; MISSING=$((MISSING + 1)); }

lab_env_set() {  # KEY VALUE … into the lab host's ~/.config/shimpaku/env, replacing or appending
  # Lines go over stdin to an explicit bash (the remote login shell may be zsh; values never sit
  # on a command line).
  local out=""
  while (($#)); do out+="$1=$2"$'\n'; shift 2; done
  printf '%s' "$out" | ssh "$LAB_HOST" bash -c "'
    f=~/.config/shimpaku/env; mkdir -p \"\$(dirname \"\$f\")\"; touch \"\$f\"; chmod 600 \"\$f\"
    while IFS= read -r kv; do [ -z \"\$kv\" ] && continue; k=\${kv%%=*}
      grep -v \"^\$k=\" \"\$f\" > \"\$f.tmp\" || true; printf \"%s\\n\" \"\$kv\" >> \"\$f.tmp\"; mv \"\$f.tmp\" \"\$f\"; chmod 600 \"\$f\"
    done'"
}

step_check() {
  MISSING=0
  echo "lab host ($LAB_HOST)"
  if ssh -o ConnectTimeout=10 -o BatchMode=yes "$LAB_HOST" true 2>/dev/null; then
    ok "ssh"
    st=$(ssh "$LAB_HOST" 'f=~/.config/shimpaku/env; d=~/.local/share/shimpaku
      echo "auth=$(grep -s ^SHIMPAKU_AUTH_MODE= $f | cut -d= -f2)"; echo "view=$(grep -s ^SHIMPAKU_PUBLIC_VIEW= $f | cut -d= -f2)"
      echo "virga=$(grep -s ^SHIMPAKU_VIRGA_LIVE= $f | cut -d= -f2)"
      echo "contact=$(grep -sc ^SHIMPAKU_PRIVACY_CONTACT= $f)"; echo "pw=$([ -s $d/admin.hash ] && echo 1 || echo 0)"
      echo "totp=$([ -s $d/totp.json ] && echo 1 || echo 0)"; echo "svc=$(systemctl --user is-active shimpakud)"')
    eval "$st"
    [[ $svc == active ]] && ok "shimpakud active" || no "shimpakud: $svc"
    [[ $pw == 1 ]] && ok "operator password set" || no "operator password (run: lab-secrets)"
    [[ $totp == 1 ]] && ok "TOTP enabled" || no "TOTP (run: lab-secrets)"
    [[ $auth == local ]] && ok "auth mode local" || no "auth mode is '${auth:-off}' (run: lab-config)"
    [[ $view == 1 ]] && ok "public read-only view on" || no "public view off (run: lab-config)"
    # optional feature: reported, never counted as missing
    [[ $virga == 1 ]] && ok "Virga live drills on" || echo "  --   Virga live drills off (run: lab-config to enable)"
  else no "cannot ssh to $LAB_HOST"; fi
  echo "VPS ($VPS_HOST)"
  if ssh -o ConnectTimeout=10 -o BatchMode=yes "$VPS_HOST" true 2>/dev/null; then
    ok "ssh with your key"
    ssh "$VPS_HOST" 'command -v docker >/dev/null && docker compose version >/dev/null 2>&1' && ok "docker + compose" \
      || no "docker + compose (VPS baseline: infra-substrate)"
    ssh "$VPS_HOST" 'command -v netbird >/dev/null && netbird status 2>/dev/null | grep -q "Management: Connected"' \
      && ok "netbird connected" || no "netbird (VPS baseline: infra-substrate; you type the setup key)"
    ssh "$VPS_HOST" "timeout 5 bash -c '</dev/tcp/${LAB_NETBIRD_IP:-0.0.0.0}/${LAB_PORT}'" 2>/dev/null \
      && ok "VPS reaches the lab over NetBird" || no "VPS cannot reach ${LAB_NETBIRD_IP:-?}:${LAB_PORT}"
  else no "cannot ssh to $VPS_HOST (run: vps-key)"; fi
  echo "settings"
  for v in DOMAINS REPO_URL LAB_NETBIRD_IP VPS_NETBIRD_IP; do
    [[ -n "${!v:-}" && "${!v}" != *"<"* ]] && ok "$v" || no "$v in $SETTINGS"; done
  echo; ((MISSING == 0)) && echo "ready: run deploy" || echo "$MISSING item(s) missing"
}

step_vps_key() {
  need VPS_ROOT VPS_PUBKEY
  local pub="${VPS_PUBKEY/#\~/$HOME}"
  [[ -s "$pub" ]] || { ssh-add -L > "$pub"; echo "exported agent public key to $pub"; }
  log "installing $(ssh-keygen -lf "$pub" | awk '{print $2, $3}') on $VPS_ROOT"
  ssh-copy-id -f -i "$pub" -o PubkeyAuthentication=no "$VPS_ROOT"
  ssh -o BatchMode=yes "$VPS_HOST" 'hostnamectl | sed -n 1,3p' && log "vps-key: ok"
}

step_lab_secrets() {
  # Interactive on the lab host's terminal; nothing passes through this script's arguments.
  local tf="$LOCAL/totp-$(date +%Y%m%d-%H%M).txt"
  ssh -t "$LAB_HOST" 'cd ~/src/shimpaku && set -a && . ~/.config/shimpaku/env && set +a
    if [ -s ~/.local/share/shimpaku/admin.hash ]; then echo "operator password already set (re-set: shimpaku admin set-password)";
    else .venv/bin/shimpaku admin set-password; fi'
  if ssh "$LAB_HOST" '[ -s ~/.local/share/shimpaku/totp.json ]'; then
    echo "TOTP already enabled (replace: shimpaku admin totp enable --force on the lab host)"
  else
    ssh -t "$LAB_HOST" 'cd ~/src/shimpaku && set -a && . ~/.config/shimpaku/env && set +a && .venv/bin/shimpaku admin totp enable' | tee "$tf"
    chmod 600 "$tf"
    log "lab-secrets: TOTP enabled; secret and recovery codes saved to $tf"
    echo; echo "Saved to $tf (mode 600, git-ignored). Move the secret and recovery codes into Bitwarden, then delete the file."
  fi
}

step_lab_config() {
  need DOMAINS LAB_NETBIRD_IP VPS_NETBIRD_IP
  lab_env_set SHIMPAKU_AUTH_MODE local SHIMPAKU_PUBLIC_VIEW 1 SHIMPAKU_BIND "$LAB_NETBIRD_IP" \
    SHIMPAKU_PORT "$LAB_PORT" SHIMPAKU_PUBLIC_URL "https://$(sib "${DOMAINS%% *}" app)" \
    SHIMPAKU_PUBLIC_CORS_ORIGINS "$(printf "https://%s," $DOMAINS | sed "s/,$//")" \
    SHIMPAKU_TRUSTED_PROXIES "$VPS_NETBIRD_IP" FORWARDED_ALLOW_IPS "$VPS_NETBIRD_IP" \
    SHIMPAKU_VIRGA_LIVE 1
  ssh "$LAB_HOST" '[ -s ~/.local/share/shimpaku/admin.hash ] && [ -s ~/.local/share/shimpaku/totp.json ]' \
    || { echo "run lab-secrets first: public view refuses operator sign-in without a password and TOTP" >&2; exit 1; }
  ssh "$LAB_HOST" 'cd ~/src/shimpaku && bash deploy/systemd/install.sh >/dev/null && systemctl --user restart shimpakud
    for i in $(seq 30); do curl -sf "http://'"$LAB_NETBIRD_IP:$LAB_PORT"'/api/whoami" >/dev/null && break; sleep 2; done
    curl -s "http://'"$LAB_NETBIRD_IP:$LAB_PORT"'/api/whoami"'
  echo; log "lab-config: lab serves on $LAB_NETBIRD_IP:$LAB_PORT, auth local, public view on, Virga live drills on"
}

step_deploy() {
  need DOMAINS REPO_URL LAB_NETBIRD_IP
  SHIMPAKU_LAB_HOST="$LAB_HOST" SHIMPAKU_VPS_HOST="$VPS_HOST" SHIMPAKU_DOMAINS="$DOMAINS" SHIMPAKU_REPO_URL="$REPO_URL" \
    SHIMPAKU_LAB_UPSTREAM="$LAB_NETBIRD_IP:$LAB_PORT" SHIMPAKU_VPS_SUDO_SECRETS="${VPS_SUDO_SECRETS/#\~/$HOME}" deploy/vps/deploy.sh "$@" 2>&1 | tee -a "$LOG"
}

step_kuma() {
  # Monitors and the public status page, through Kuma's admin socket.io API (scripts/kuma_setup.mjs,
  # idempotent). The admin UI answers on the VPS's NetBird address, so this Mac must be on NetBird.
  need DOMAINS VPS_NETBIRD_IP
  command -v node >/dev/null || { echo "node is needed on this machine (scripts/kuma_setup.mjs)" >&2; exit 1; }
  local url="${KUMA_URL:-http://$VPS_NETBIRD_IP:3001}" creds="${KUMA_CREDS:-.local/kuma-admin.txt}"
  [[ "$creds" = /* ]] || creds="$ROOT/${creds#./}"
  [[ -s "$creds" ]] || { echo "no Kuma admin credentials at $creds: line 1 the user, line 2 the password (git-ignored; a fresh Kuma gets this admin created)" >&2; exit 1; }
  local mode; mode=$(stat -f %Lp "$creds" 2>/dev/null || stat -c %a "$creds")   # BSD, then GNU
  (( (8#$mode & 8#077) == 0 )) || { echo "$creds is readable by others: chmod 600 it" >&2; exit 1; }
  # either labelled lines (`username: …`, `password: …`) or the bare user on line 1 and password on line 2
  local u p; u=$(sed -n 's/^username: *//p' "$creds" | head -1); p=$(sed -n 's/^password: *//p' "$creds" | head -1)
  [[ -n "$u" ]] || { IFS= read -r u; IFS= read -r p; } < "$creds"
  [[ -n "$u" && -n "$p" ]] || { echo "$creds needs 'username: …' and 'password: …' lines (or the user on line 1, the password on line 2)" >&2; exit 1; }
  [[ -d scripts/node_modules/socket.io-client ]] || (cd scripts && npm ci --omit=dev --ignore-scripts --no-audit --no-fund >/dev/null)
  local d slug="${KUMA_SLUG:-shimpaku}" sites=()
  # bare host URLs: the monitor names (the URL without scheme) match the ones already in Kuma; the status
  # host's / redirects to /status/$slug, which the monitor follows
  for d in $DOMAINS; do sites+=("https://$d" "https://$(sib "$d" app)" "https://$(sib "$d" status)"); done
  log "kuma: $url, website monitors for: $DOMAINS; Live lab on https://$(sib "${DOMAINS%% *}" app)"
  # the credentials live only in this one process's environment (never argv, never echoed)
  KUMA_URL="$url" KUMA_USER="$u" KUMA_PASS="$p" KUMA_SLUG="$slug" \
    node scripts/kuma_setup.mjs --app "https://$(sib "${DOMAINS%% *}" app)" "${sites[@]}" 2>&1 | tee -a "$LOG"
  [[ ${PIPESTATUS[0]} -eq 0 ]] || { echo "kuma setup failed (is this machine on NetBird, and is $url the Kuma admin address?)" >&2; exit 1; }
  log "kuma: done; status page https://$(sib "${DOMAINS%% *}" status)/"
}

step_verify() {
  need DOMAINS
  local d; for d in $DOMAINS; do echo "== $d"; local a="https://$(sib $d app)" DOMAIN=$d
  curl -fsS -o /dev/null -w "landing %{http_code}\n" "https://$DOMAIN/" | tee -a "$LOG"
  curl -fsS "$a/api/whoami" | python3 -c 'import json,sys; w=json.load(sys.stdin); print("whoami:", w.get("role") or w.get("kind"), "replay" if w.get("replay") else "live")' | tee -a "$LOG"
  echo "mcp without token: $(curl -s -o /dev/null -w '%{http_code}' -X POST "$a/mcp")" | tee -a "$LOG"
  echo "anonymous approve: $(curl -s -o /dev/null -w '%{http_code}' -X POST "$a/api/actions/x/approve")" | tee -a "$LOG"
  # Virga live: the anonymous health read Kuma's Live lab monitors use, and that nobody anonymous can inject
  curl -fsS "$a/api/public/health" | python3 -c 'import json,sys; h=json.load(sys.stdin); print("health:", " ".join(f"{k}={h.get(k)}" for k in ("campus", "fabric", "drill", "lab")))' | tee -a "$LOG" \
    || echo "health: /api/public/health not answering (the lab daemon needs the Virga live build: run lab-config)" | tee -a "$LOG"
  echo "anonymous virga inject: $(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{}' "$a/api/virga/trials") (want 401/403; 503 = lab offline, 404 = daemon without Virga live)" | tee -a "$LOG"
  done
}

case "${1:-}" in
  check) step_check ;; vps-key) step_vps_key ;; lab-secrets) step_lab_secrets ;; lab-config) step_lab_config ;;
  deploy) shift; step_deploy "$@" ;; kuma) step_kuma ;; verify) step_verify ;;
  *) sed -n 2,16p "$0"; exit 2 ;;
esac
