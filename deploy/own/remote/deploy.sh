#!/usr/bin/env bash
# "Deploy your own" — the build, run as root on the hypervisor (docs/build/deploy-your-own.md §3).
# install.sh ships this bundle to /var/lib/shimpaku-own/bundle and runs it in two stages:
#
#   deploy.sh --stage base   packages, nested KVM, libvirtd, the operator user, the pinned repos,
#                            the lab data dir, images already on the host
#   (install.sh uploads the visitor's local image files into the lab image dir here)
#   deploy.sh --stage rest   uv + gnmic + Shimpaku's venv, the lab per topology, device configs,
#                            Shimpaku's env + operator password + user unit, the health proof
#
# Options: --profile full|fabric|campus --operator NAME --data-dir DIR --bind ADDR
#          [--router-image HOSTPATH] [--switch-image HOSTPATH] [--upload KIND=NAME:BYTES]
#          [--enable-nested] [--selftest] [--dry-run [--os apt|pacman|dnf]]
#
# Every mutating command is printed ("+ ...") before it runs, and every thing it creates is
# recorded in the manifest (remote/ownlib.py) BEFORE it is created, so --uninstall can always
# remove it. Every step checks first and skips what is already there: a re-run resumes.
# --dry-run prints the commands for a fresh host and runs none of them (no host needed).
set -euo pipefail
export LC_ALL=C DEBIAN_FRONTEND=noninteractive
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BUNDLE=$(dirname "$HERE")
STATE=/var/lib/shimpaku-own
MANIFEST=$STATE/manifest.json
OWNLIB=$HERE/ownlib.py
RBUNDLE=$BUNDLE   # where the bundle sits on the host (printed paths on a dry run)
# shellcheck disable=SC1091
. "$BUNDLE/PINS"
: "${LAB_REPO:?PINS}" "${LAB_COMMIT:?PINS}" "${SHIMPAKU_REPO:?PINS}" "${SHIMPAKU_COMMIT:?PINS}"

STAGE="" PROFILE=full OP=shimpaku DATA=/home/lab BIND=127.0.0.1 NESTED=0 SELFTEST=0 DRY=0 DRY_OS=apt
IMG_ROUTER="" IMG_SWITCH="" UPLOADS=()
while (($#)); do
  case $1 in
    --stage) STAGE=$2; shift ;;
    --profile) PROFILE=$2; shift ;;
    --operator) OP=$2; shift ;;
    --data-dir) DATA=$2; shift ;;
    --bind) BIND=$2; shift ;;
    --router-image) IMG_ROUTER=$2; shift ;;
    --switch-image) IMG_SWITCH=$2; shift ;;
    --upload) UPLOADS+=("$2"); shift ;;
    --enable-nested) NESTED=1 ;;
    --selftest) SELFTEST=1 ;;
    --dry-run) DRY=1 ;;
    --os) DRY_OS=$2; shift ;;
    *) echo "deploy.sh: unknown option $1" >&2; exit 64 ;;
  esac
  shift
done
[[ $STAGE == base || $STAGE == rest ]] || { echo "deploy.sh: --stage base|rest" >&2; exit 64; }
((DRY)) && RBUNDLE=$STATE/bundle
if ((!DRY)) && [[ $(id -u) != 0 ]]; then echo "deploy.sh: run as root" >&2; exit 1; fi

# ---- helpers ------------------------------------------------------------------------------------
say() { printf '\n== %s\n' "$*"; }
q() { local out="" a; for a in "$@"; do out+="$(printf '%q' "$a") "; done; printf '%s' "${out% }"; }
run() { printf '+ %s\n' "$(q "$@")"; ((DRY)) || "$@"; }
# state probe: on a dry run the host is assumed fresh, so every probe says "not there yet"
probe() { ((DRY)) && return 1; "$@" >/dev/null 2>&1; }
rec() {  # kind name [k=v ...]: recorded before the thing is created
  if ((DRY)); then printf '+ manifest += %s %s\n' "$1" "$2"; return; fi
  python3 "$OWNLIB" manifest-add "$MANIFEST" "$@"
}
lib() { python3 "$OWNLIB" "$@"; }
OPHOME=/home/$OP
opuid() { if ((DRY)); then echo '<uid>'; else id -u "$OP"; fi; }
as_op() {  # run a bash snippet as the operator, with its user systemd instance reachable
  local uid; uid=$(opuid)
  printf '+ [as %s] %s\n' "$OP" "$1"
  ((DRY)) && return 0
  runuser -u "$OP" -- env -i HOME="$OPHOME" USER="$OP" LOGNAME="$OP" LANG=C.UTF-8 \
    XDG_RUNTIME_DIR="/run/user/$uid" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" \
    PATH="$OPHOME/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin" \
    LAB_USER="${LAB_USER:-admin}" LAB_PASSWORD="${LAB_PASSWORD:-admin@123}" \
    bash -c "set -euo pipefail; $1"
}
as_op_q() { ((DRY)) && return 1; as_op "$1" >/dev/null 2>&1; }   # an as-operator probe
# shellcheck disable=SC2016  # ${Status} is dpkg-query's format, not ours
apt_has() { ((DRY)) && return 1; [[ $(dpkg-query -W -f='${Status}' "$1" 2>/dev/null) == *"ok installed" ]]; }
pkg_mgr() {
  if ((DRY)); then echo "$DRY_OS"; return; fi
  local m; for m in apt-get:apt pacman:pacman dnf:dnf; do command -v "${m%%:*}" >/dev/null && { echo "${m##*:}"; return; }; done
  echo none
}
canonical_image() {  # kind path -> the name lab-gen.py's glob finds (vJunos-<kind>-*.qcow2)
  local kind=$1 base v
  base=$(basename "$2")
  v=$(grep -oE '[0-9]+\.[0-9]+R[0-9]+(-S[0-9]+)?\.[0-9]+' <<<"$base" | head -1 || true)
  printf 'vJunos-%s-%s.qcow2' "$kind" "${v:-own}"
}
LABREPO=$OPHOME/juniper-7-day-sprint
SRC=$OPHOME/src/shimpaku
VENV_PY=$SRC/.venv/bin/python
TOPOS=()
while IFS= read -r t; do TOPOS+=("$t"); done < <(lib topologies --profile "$PROFILE")

qemu_group() {  # the group QEMU runs as; the lab data dir is group-owned by it (lab-host-setup.sh)
  ((DRY)) && { echo QEMU_GROUP; return; }
  local g=""
  [[ -r /etc/libvirt/qemu.conf ]] && g=$(sed -n 's/^[[:space:]]*group[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' /etc/libvirt/qemu.conf | tail -1)
  if [[ -z $g ]]; then
    if id libvirt-qemu >/dev/null 2>&1; then g=$(id -gn libvirt-qemu)      # Debian, Ubuntu
    elif id qemu >/dev/null 2>&1; then g=$(id -gn qemu)                     # Fedora, Arch
    else g=libvirt; fi
  fi
  echo "$g"
}

# ---- stage base -------------------------------------------------------------------------------------
packages() {
  say "1. packages"
  local pm; pm=$(pkg_mgr)
  local want=()
  case $pm in
    apt) want=(qemu-system-x86 qemu-utils libvirt-daemon-system libvirt-clients dosfstools mtools python3 git curl ca-certificates nftables iproute2) ;;
    pacman) want=(qemu-base libvirt dosfstools mtools python git curl nftables iproute2) ;;
    dnf) want=(qemu-system-x86-core qemu-img libvirt-daemon-kvm libvirt-client dosfstools mtools python3 git curl nftables iproute) ;;
    *) echo "no package manager this installer knows (apt, pacman, dnf)" >&2; exit 3 ;;
  esac
  local missing=() p
  for p in "${want[@]}"; do
    case $pm in
      apt) apt_has "$p" && continue ;;
      pacman) probe pacman -Q "$p" && continue ;;
      dnf) probe rpm -q "$p" && continue ;;
    esac
    missing+=("$p")
  done
  if ((${#missing[@]} == 0)); then echo "   all present"; return; fi
  case $pm in
    apt) run apt-get update -q; run apt-get install -y -q "${missing[@]}" ;;
    pacman) run pacman -S --needed --noconfirm "${missing[@]}" ;;
    dnf) run dnf install -y -q "${missing[@]}" ;;
  esac
  # recorded after the install: the manifest needs python3, which may be one of them
  for p in "${missing[@]}"; do rec package "$p" "manager=$pm"; done
}

nested_kvm() {
  say "2. nested KVM"
  local mod="" f
  for f in kvm_intel kvm_amd; do [[ -r /sys/module/$f/parameters/nested ]] && mod=$f; done
  if ((DRY)); then
    ((NESTED)) || { echo "   on (preflight checked it; --enable-nested shows the change)"; return; }
    mod=kvm_intel
  fi
  if ! ((DRY)) && [[ -n $mod && $(cat "/sys/module/$mod/parameters/nested") =~ ^(Y|y|1)$ ]]; then
    echo "   $mod nested is on"; return
  fi
  ((NESTED)) || { echo "nested KVM is off; re-run with --enable-nested" >&2; exit 3; }
  [[ -n $mod ]] || { echo "no kvm_intel/kvm_amd module loaded" >&2; exit 3; }
  local conf=/etc/modprobe.d/kvm-nested.conf
  if [[ -e $conf ]] && ! lib manifest-has "$MANIFEST" file "$conf"; then
    echo "$conf exists and is not ours; set nested=1 there yourself" >&2; exit 3
  fi
  rec file "$conf"
  if ((DRY)); then printf '+ printf "options %s nested=1\\n" > %s\n' "$mod" "$conf"; else printf 'options %s nested=1\n' "$mod" > "$conf"; fi
  if probe pgrep -f qemu-system; then
    echo "VMs are running: reboot the host (nested KVM is set for the next boot), then re-run" >&2
    exit 4
  fi
  run modprobe -r "$mod"
  run modprobe "$mod"
}

libvirtd() {
  say "3. libvirt"
  if probe virsh -c qemu:///system uri; then echo "   qemu:///system answers"; return; fi
  local unit=libvirtd.service
  if ! ((DRY)) && ! systemctl cat libvirtd.service >/dev/null 2>&1; then unit=virtqemud.socket; fi
  probe systemctl is-enabled "$unit" || rec service "$unit"
  run systemctl enable --now "$unit"
}

operator_user() {
  say "4. operator user $OP"
  QGROUP=$(qemu_group)
  if ! probe id "$OP"; then
    rec user "$OP"
    run useradd --create-home --user-group --shell /bin/bash "$OP"
  fi
  local g
  for g in libvirt kvm "$QGROUP"; do
    ((DRY)) || getent group "$g" >/dev/null || continue
    probe sh -c "id -nG $(q "$OP") | tr ' ' '\n' | grep -qx $(q "$g")" && continue
    rec group-member "$OP:$g"
    run usermod -aG "$g" "$OP"
  done
  local linger=""
  ((DRY)) || linger=$(loginctl show-user "$OP" -p Linger --value 2>/dev/null || true)
  if [[ $linger != yes ]]; then
    rec linger "$OP"
    run loginctl enable-linger "$OP"
  fi
  if ! ((DRY)); then
    local _; for _ in $(seq 30); do [[ -S /run/user/$(id -u "$OP")/bus ]] && break; sleep 1; done
  fi
}

clone() {  # url commit dir
  local url=$1 commit=$2 dir=$3
  local cur=""
  ((DRY)) || cur=$(runuser -u "$OP" -- git -C "$dir" rev-parse HEAD 2>/dev/null || true)
  if [[ $cur == "$commit" ]]; then
    echo "   $dir at ${commit:0:12}"; return
  fi
  rec repo "$dir" "commit=$commit"
  as_op "mkdir -p $(q "$dir") && cd $(q "$dir") && { [ -d .git ] || git init -q; } && { git remote get-url origin >/dev/null 2>&1 || git remote add origin $(q "$url"); } && git fetch -q --depth 1 origin $(q "$commit") && git -c advice.detachedHead=false checkout -q --detach FETCH_HEAD"
}

repos() {
  say "5. sources at the pins (deploy/own/PINS)"
  clone "$LAB_REPO" "$LAB_COMMIT" "$LABREPO"
  clone "$SHIMPAKU_REPO" "$SHIMPAKU_COMMIT" "$SRC"
}

data_dir() {
  say "6. lab data dir $DATA (ops/lab-host-setup.sh)"
  if ! lib manifest-has "$MANIFEST" dir "$DATA" 2>/dev/null; then
    if ! ((DRY)) && [[ -e $DATA ]]; then echo "$DATA exists and is not ours" >&2; exit 3; fi
    rec dir "$DATA"
  fi
  run env LAB_OWNER="$OP" LAB_GROUP="$QGROUP" LAB_DATA="$DATA" bash "$LABREPO/ops/lab-host-setup.sh"
}

host_images() {
  say "7. images already on the host"
  local kind src dst
  for kind in router switch; do
    src=$IMG_ROUTER; [[ $kind == switch ]] && src=$IMG_SWITCH
    [[ -z $src ]] && continue
    dst=$DATA/images/$(canonical_image "$kind" "$src")
    if [[ $src == "$dst" ]]; then echo "   $dst (in place)"; continue; fi
    if ! ((DRY)) && [[ -f $dst && $(stat -c %s "$src") == "$(stat -c %s "$dst")" ]]; then echo "   $dst present"; continue; fi
    rec image "$dst"
    run cp --sparse=always "$src" "$dst.part"
    run mv "$dst.part" "$dst"
    run chown "$OP:$QGROUP" "$dst"
    run chmod 0640 "$dst"
  done
}

# ---- stage rest -------------------------------------------------------------------------------------
finish_uploads() {
  local u kind name size dst
  for u in ${UPLOADS[@]+"${UPLOADS[@]}"}; do
    kind=${u%%=*}; name=${u#*=}; size=${name##*:}; name=${name%:*}
    dst=$DATA/images/$name
    if probe test -f "$dst"; then continue; fi
    if ! ((DRY)) && [[ $(stat -c %s "$dst.part" 2>/dev/null) != "$size" ]]; then
      echo "upload of the $kind image is incomplete ($dst.part); re-run to resume" >&2; exit 3
    fi
    run mv "$dst.part" "$dst"
    run chown "$OP:$QGROUP" "$dst"
    run chmod 0640 "$dst"
  done
}

tools() {
  say "8. uv $UV_VERSION, gnmic $GNMIC_VERSION, Shimpaku's venv"
  if ! as_op_q "command -v uv"; then
    rec file "$OPHOME/.local/bin/uv"
    as_op "d=\$(mktemp -d) && cd \"\$d\" && curl -fsSLo uv.tgz https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-x86_64-unknown-linux-gnu.tar.gz && echo '$UV_SHA256  uv.tgz' | sha256sum -c --quiet - && tar xzf uv.tgz && mkdir -p ~/.local/bin && install -m 0755 uv-x86_64-unknown-linux-gnu/uv uv-x86_64-unknown-linux-gnu/uvx ~/.local/bin/ && rm -rf \"\$d\""
  fi
  if ! as_op_q "\$HOME/.local/bin/aiops-gnmic version | grep -q '$GNMIC_VERSION'"; then
    rec file "$OPHOME/.local/bin/aiops-gnmic"
    as_op "d=\$(mktemp -d) && cd \"\$d\" && curl -fsSLo gnmic.tgz https://github.com/openconfig/gnmic/releases/download/v$GNMIC_VERSION/gnmic_${GNMIC_VERSION}_Linux_x86_64.tar.gz && echo '$GNMIC_SHA256  gnmic.tgz' | sha256sum -c --quiet - && tar xzf gnmic.tgz gnmic && mkdir -p ~/.local/bin && install -m 0755 gnmic ~/.local/bin/aiops-gnmic && rm -rf \"\$d\""
  fi
  as_op "cd ~/src/shimpaku && { [ -x .venv/bin/python ] || uv venv -q -p 3.12 .venv; } && uv pip install -q -p .venv -e '.[junos]'"
}

topo_nodes() { lib nodes --profile "$PROFILE" | awk -v t="$1" '$3 == t {print $1"="$2}'; }

lab_up() {
  local t n b
  for t in "${TOPOS[@]}"; do
    say "9. lab: $t"
    as_op "cd ~/juniper-7-day-sprint && python3 ops/lab-gen.py --topology lab/topologies/$t.toml --check"
    as_op "cd ~/juniper-7-day-sprint && python3 ops/lab-gen.py --topology lab/topologies/$t.toml --net-only"
    for b in $(lib bridges --topology "$t"); do rec bridge "$b"; done
    rec nft-table "inet lab_iso"
    # the lab's own net script (bridges + the lab_iso isolation table), as root; lab.sh then
    # finds lab-mgmt up and never needs sudo
    run bash "$LABREPO/artifacts/lab-net-up.sh"
    for n in $(topo_nodes "$t"); do rec domain "lab-${n%%=*}"; done
    as_op "cd ~/juniper-7-day-sprint && ops/lab.sh up lab/topologies/$t.toml"
  done
}

apply_args() {  # topology [services-only] -> apply_sets.py --node args
  local t=$1 only=${2:-} n out=()
  for n in $(topo_nodes "$t"); do
    out+=(--node "$n")
    [[ $only == services ]] || [[ $t != day-02-switching ]] || out+=("$RBUNDLE/configs/campus/${n%%=*}.set")
    out+=("$RBUNDLE/configs/services/${n%%=*}.set")
  done
  q "${out[@]}"
}

configure() {
  local t
  for t in "${TOPOS[@]}"; do
    say "10. device configs: $t (vJunos boots in 5-15 min; NETCONF answering is the signal)"
    case $t in
      day-02-switching)
        if ! as_op "$VENV_PY $RBUNDLE/remote/apply_sets.py --wait-only --wait 1500 $(apply_args "$t" services)"; then
          # the vendor config disk did not take: the lab's console bootstrap (ordinals 1-3 are right here)
          local n; for n in r1 r2 sw1; do as_op "cd ~/juniper-7-day-sprint && python3 ops/lab-bootstrap.py --topology lab/topologies/$t.toml --node $n"; done
        fi
        as_op "$VENV_PY $RBUNDLE/remote/apply_sets.py --wait 900 --assert-campus $(apply_args "$t")"
        ;;
      day-06-datacenter)
        # lab-bootstrap.py ignores ordinal_base (it would talk to the campus consoles), so there is no
        # console fallback for the fabric: the boot config disk has to take
        as_op "$VENV_PY $RBUNDLE/remote/apply_sets.py --wait-only --wait 1800 $(apply_args "$t" services)"
        as_op "cd ~/juniper-7-day-sprint && $VENV_PY day-07/automation/gen_fabric.py lab/topologies/$t.toml && $VENV_PY day-07/automation/deploy.py lab/topologies/$t.toml"
        as_op "$VENV_PY $RBUNDLE/remote/apply_sets.py $(apply_args "$t" services)"
        ;;
    esac
  done
}

shimpaku() {
  say "11. Shimpaku: env, operator password, user unit"
  local topos="" t
  for t in "${TOPOS[@]}"; do topos+="$t.toml,"; done
  rec file "$OPHOME/.config/shimpaku/env"
  # KEY=VALUE lines over stdin (the ops/golive/run.sh lab_env_set pattern): values never sit on a
  # command line. The device credentials are the lab's published boot defaults (lab-gen.py).
  local kv="SHIMPAKU_DEVICE_USER=admin
SHIMPAKU_DEVICE_PASSWORD=admin@123
SHIMPAKU_AUTH_MODE=local
SHIMPAKU_BIND=$BIND
SHIMPAKU_VIRGA_LIVE=1
SHIMPAKU_TOPOLOGIES=${topos%,}
SHIMPAKU_LAB_REPO=$LABREPO
SHIMPAKU_LOCK_DB=$DATA/shimpaku/locks.db
"
  # shellcheck disable=SC2016  # expanded by the operator's bash, not here
  local setenv='f=~/.config/shimpaku/env; mkdir -p "$(dirname "$f")"; touch "$f"; chmod 600 "$f"
    while IFS= read -r kv; do [ -z "$kv" ] && continue; k=${kv%%=*}
      grep -v "^$k=" "$f" > "$f.tmp" || true; printf "%s\n" "$kv" >> "$f.tmp"; mv "$f.tmp" "$f"; chmod 600 "$f"; done'
  if ((DRY)); then
    printf '+ [as %s] write ~/.config/shimpaku/env (0600): %s\n' "$OP" "$(printf '%s' "$kv" | cut -d= -f1 | paste -sd' ' -)"
  else
    printf '%s' "$kv" | as_op "$setenv" >/dev/null
    echo "+ [as $OP] ~/.config/shimpaku/env: $(printf '%s' "$kv" | cut -d= -f1 | paste -sd' ' -)"
  fi
  if ! probe test -s "$OPHOME/.local/share/shimpaku/admin.hash"; then
    if ((DRY)); then
      printf '+ [as %s] ~/src/shimpaku/.venv/bin/shimpaku admin set-password --stdin   (stdin: a generated password, printed once at the end)\n' "$OP"
    else
      local pw; pw=$(python3 -c 'import secrets; print(secrets.token_urlsafe(15))')
      printf '%s\n' "$pw" | as_op "cd ~/src/shimpaku && .venv/bin/shimpaku admin set-password --stdin"
      ( umask 077; printf '%s\n' "$pw" > "$STATE/operator-password" )
      pw=""
    fi
  fi
  rec user-unit shimpakud
  as_op "cd ~/src/shimpaku && bash deploy/systemd/install.sh >/dev/null && systemctl --user restart shimpakud.service && systemctl --user is-active shimpakud.service"
}

proof() {
  say "12. proof: /api/public/health"
  local host=$BIND sites
  [[ $host == 0.0.0.0 || $host == "::" ]] && host=127.0.0.1
  sites=$(for t in "${TOPOS[@]}"; do [[ $t == day-02-switching ]] && echo campus || echo fabric; done | paste -sd, -)
  as_op "for i in \$(seq 90); do
      h=\$(curl -fsS http://$host:8780/api/public/health 2>/dev/null || true)
      if [ -n \"\$h\" ] && printf '%s' \"\$h\" | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if all(d.get(s)==\"healthy\" for s in \"$sites\".split(\",\")) else 1)'; then echo \"   healthy: \$h\"; exit 0; fi
      sleep 10; done; echo \"   not healthy after 15 min: \${h:-no answer}\"; exit 1" || echo "WARN: the lab is up but $sites did not read healthy yet (telemetry can take a few minutes more)"
  if ((SELFTEST)); then
    if [[ " ${TOPOS[*]} " == *" day-06-datacenter "* ]]; then
      say "13. Virga self-test FAB-01 (inject a fabric link fault on leaf1, prove, revert, prove)"
      as_op "set -a; . ~/.config/shimpaku/env; set +a; cd ~/src/shimpaku && .venv/bin/virga --have day-06-fabric selftest --only FAB-01"
    else
      echo "--selftest needs the fabric (FAB-01); skipped for profile $PROFILE"
    fi
  fi
}

if ((DRY)); then echo "# dry run: the commands a fresh $DRY_OS host would get, in order; nothing runs"; fi
((DRY)) || { install -d -m 0755 "$STATE"; }
QGROUP=$(qemu_group)
case $STAGE in
  base) packages; nested_kvm; libvirtd; operator_user; repos; data_dir; host_images ;;
  rest) finish_uploads; tools; lab_up; configure; shimpaku; proof ;;
esac
echo
echo "stage $STAGE: done"
