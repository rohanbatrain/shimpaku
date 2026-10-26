#!/usr/bin/env bash
# Deploy your own Shimpaku lab: 7 vJunos devices + the Shimpaku daemon + Virga, on YOUR hypervisor.
# The contract is docs/build/deploy-your-own.md; the reference is deploy/own/README.md.
#
#   deploy/own/install.sh --host <ip> [--check] [--profile full|fabric|campus]
#       [--router-image F] [--switch-image F] [--key F] [--user U] [--port N] [--ssh-alias A]
#       [--local] [--operator NAME] [--enable-nested] [--i-know-this-is-a-vm] [--bind ADDR]
#       [--selftest] [--yes] [--json] [--dry-run] [--uninstall [--keep-packages]] [--remove-key]
#
# Runs on the visitor's own computer (macOS or Linux, bash 3.2+) and talks to the hypervisor over
# SSH, or on the hypervisor itself with --local. The root password is read once from the terminal,
# used only to install a fresh ed25519 key, and never echoed, logged, written or put in argv/env.
#
# Exit: 0 done / all checks pass, 1 warnings only (--check), 2 a preflight check failed,
#       3 a deploy step failed, 4 reboot needed (nested KVM), 5 aborted, 64 usage.
set -euo pipefail
umask 077

HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck disable=SC1091
. "$HERE/PINS"
# The Shimpaku commit the host clones: the one this copy was downloaded at (get.sh exports SHIMPAKU_REF),
# else this clone's own HEAD, else the last tested pin in PINS. Preflight FAILs if it is not on GitHub.
if [[ "${SHIMPAKU_REF:-}" =~ ^[0-9a-f]{40}$ ]]; then
  SHIMPAKU_COMMIT=$SHIMPAKU_REF
elif _head=$(git -C "$HERE" rev-parse HEAD 2>/dev/null) && [[ "$_head" =~ ^[0-9a-f]{40}$ ]]; then
  SHIMPAKU_COMMIT=$_head
fi

usage() { sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; }
die() { printf 'error: %s\n' "$1" >&2; exit "${2:-3}"; }
usage_die() { printf 'error: %s\n' "$1" >&2; echo "see: $0 --help" >&2; exit 64; }
say() { printf '\n== %s\n' "$*" >&2; }      # progress goes to stderr: --json keeps stdout clean
note() { printf '   %s\n' "$*" >&2; }

# ---- options ---------------------------------------------------------------------------------------
HOST="" ALIAS="" SSH_USER=root PORT=22 KEY="" KEY_GIVEN=0 LOCAL=0 CHECK=0 JSON=0 DRY=0 YES=0
PROFILE="" PROFILE_GIVEN=0 IMG_ROUTER="" IMG_SWITCH="" OP=shimpaku BIND=127.0.0.1 NESTED=0 ALLOW_VM=0
SELFTEST=0 UNINSTALL=0 KEEP_PKGS=0 REMOVE_KEY=0 DRY_OS=apt DATA=/home/lab
ORIG_ARGS=("$@")
need_arg() { [ "$#" -ge 2 ] && [ -n "$2" ] || usage_die "$1 needs a value"; }
while [ "$#" -gt 0 ]; do
  case $1 in
    --host) need_arg "$@"; HOST=$2; shift ;;
    --ssh-alias) need_arg "$@"; ALIAS=$2; shift ;;
    --user) need_arg "$@"; SSH_USER=$2; shift ;;
    --port) need_arg "$@"; PORT=$2; shift ;;
    --key) need_arg "$@"; KEY=$2; KEY_GIVEN=1; shift ;;
    --local) LOCAL=1 ;;
    --check) CHECK=1 ;;
    --json) JSON=1 ;;
    --dry-run) DRY=1 ;;
    --dry-run-os) need_arg "$@"; DRY_OS=$2; shift ;;
    --profile) need_arg "$@"; PROFILE=$2; PROFILE_GIVEN=1; shift ;;
    --router-image) need_arg "$@"; IMG_ROUTER=$2; shift ;;
    --switch-image) need_arg "$@"; IMG_SWITCH=$2; shift ;;
    --operator) need_arg "$@"; OP=$2; shift ;;
    --bind) need_arg "$@"; BIND=$2; shift ;;
    --enable-nested) NESTED=1 ;;
    --i-know-this-is-a-vm) ALLOW_VM=1 ;;
    --selftest) SELFTEST=1 ;;
    --yes|-y) YES=1 ;;
    --uninstall) UNINSTALL=1 ;;
    --keep-packages) KEEP_PKGS=1 ;;
    --remove-key) REMOVE_KEY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage_die "unknown option $1" ;;
  esac
  shift
done
PROFILE=${PROFILE:-full}
case $PROFILE in full|fabric|campus) ;; *) usage_die "--profile is full, fabric or campus" ;; esac
case $OP in *[!a-z0-9_-]*|-*|"") usage_die "--operator: lower-case letters, digits, - and _ only" ;; esac
case $PORT in *[!0-9]*|"") usage_die "--port is a number" ;; esac
[ -n "$HOST" ] && [ -n "$ALIAS" ] && usage_die "--host and --ssh-alias are alternatives"
{ [ -n "$HOST" ] || [ -n "$ALIAS" ]; } && [ "$LOCAL" = 1 ] && usage_die "--local deploys to this machine: no --host"
if [ -n "$HOST" ]; then
  case $HOST in *[!A-Za-z0-9.:_-]*|-*) usage_die "--host: an IP address or host name" ;; esac
fi
# Every value below ends up in an ssh argument or a command string run on the host: a leading '-'
# would be read by ssh as an option (-oProxyCommand=...), a ';' or newline as a second command.
case $SSH_USER in *[!A-Za-z0-9._-]*|-*|"") usage_die "--user: a user name (letters, digits, . _ -)" ;; esac
case $ALIAS in *[!A-Za-z0-9.:_-]*|-*) usage_die "--ssh-alias: a Host name from your ssh config" ;; esac
case $BIND in localhost) ;; *[!0-9A-Fa-f.:]*|"") usage_die "--bind: an IP address (127.0.0.1, 0.0.0.0, ::1, ...)" ;; esac
case $IMG_ROUTER$IMG_SWITCH$KEY in *[[:cntrl:]]*) usage_die "an image or key path contains a control character" ;; esac

# ---- the terminal (stdin may be this script, piped from curl) --------------------------------------
have_tty() { (exec </dev/tty) 2>/dev/null; }
ask() {  # VAR "question" [default]
  local __v __d=${3:-}
  have_tty || die "no terminal to ask '$2': pass the flags (--host, --profile, --router-image/--switch-image) and --yes" 64
  if [ -n "$__d" ]; then printf '%s [%s]: ' "$2" "$__d" >/dev/tty; else printf '%s: ' "$2" >/dev/tty; fi
  IFS= read -r __v </dev/tty || __v=""
  [ -z "$__v" ] && __v=$__d
  printf -v "$1" '%s' "$__v"
}

# ---- mode: ssh to a host, or this machine ---------------------------------------------------------
interactive_setup() {
  if [ "$(uname -s)" = Linux ] && [ -e /dev/kvm ]; then
    local a; ask a "This machine has /dev/kvm. Deploy the lab HERE, on this machine? (y/n)" y
    case $a in y|Y|yes) LOCAL=1 ;; esac
  fi
  if [ "$LOCAL" = 0 ]; then
    ask HOST "Hypervisor address (IP or name)"
    [ -n "$HOST" ] || die "no host given" 64
    case $HOST in *[!A-Za-z0-9.:_-]*|-*) die "not an IP address or host name: $HOST" 64 ;; esac
  fi
  if [ "$PROFILE_GIVEN" = 0 ]; then
    ask PROFILE "Profile: full (7 devices, ~39 GiB RAM), fabric (4, ~24 GiB), campus (3, ~19 GiB)" full
    case $PROFILE in full|fabric|campus) ;; *) die "unknown profile $PROFILE" 64 ;; esac
  fi
}
if [ "$LOCAL" = 0 ] && [ -z "$HOST" ] && [ -z "$ALIAS" ] && [ "$DRY" = 0 ]; then
  [ "$YES" = 1 ] && ! have_tty && die "no --host (or --local) and no terminal to ask for one" 64
  interactive_setup
fi
needs_router() { [ "$PROFILE" != fabric ]; }
if [ "$DRY" = 0 ] && [ "$UNINSTALL" = 0 ] && [ "$CHECK" = 0 ] && have_tty && [ "$YES" = 0 ]; then
  needs_router && [ -z "$IMG_ROUTER" ] && ask IMG_ROUTER "vJunos-router qcow2 (a file on this computer, or a path on the hypervisor)"
  [ -z "$IMG_SWITCH" ] && ask IMG_SWITCH "vJunos-switch qcow2 (a file on this computer, or a path on the hypervisor)"
fi
needs_router || IMG_ROUTER=""

if [ "$LOCAL" = 1 ] && [ "$DRY" = 0 ] && [ "$CHECK" = 0 ] && [ "$(id -u)" != 0 ]; then
  # local mode needs root; sudo asks for its own password on the terminal (we never see it)
  command -v sudo >/dev/null || die "--local needs root: run as root" 64
  echo "local mode needs root: re-running under sudo"
  exec sudo -- bash "$0" --local ${ORIG_ARGS[@]+"${ORIG_ARGS[@]}"}
fi

LABEL=${ALIAS:-${HOST:-local}}
[ "$DRY" = 1 ] && LABEL=${ALIAS:-${HOST:-dry-run}}
STATE_DIR=${XDG_STATE_HOME:-$HOME/.local/state}/shimpaku-own
mkdir -p "$STATE_DIR"
LOG=$STATE_DIR/$LABEL.log
# Everything below goes to the terminal and to the log, except the operator password (fd 3 only).
# Nothing secret is ever printed on fds 1/2: the root password is read from /dev/tty and kept in
# one shell variable, which is cleared after its single use.
exec 3>&1
exec > >(tee -a "$LOG") 2> >(tee -a "$LOG" >&2)
printf '\n---- %s  install.sh %s\n' "$(date '+%F %T')" "$(printf '%q ' ${ORIG_ARGS[@]+"${ORIG_ARGS[@]}"})" >&2

# ---- transport ------------------------------------------------------------------------------------
KNOWN=$STATE_DIR/known_hosts
SUDO=""
TARGET_DESC=""
ssh_cmd() {  # sets SSH (batch: key only, never prompts) and SSH_I (may prompt, for the one sudo)
  if [ -n "$ALIAS" ]; then
    SSH_I=(ssh -o ConnectTimeout=15 -o ServerAliveInterval=30 "$ALIAS")
    TARGET_DESC="ssh alias $ALIAS"
  else
    SSH_I=(ssh -o ConnectTimeout=15 -o ServerAliveInterval=30 -o IdentitiesOnly=yes
         -i "$KEY" -o UserKnownHostsFile="$KNOWN" -o StrictHostKeyChecking=accept-new -p "$PORT" "$SSH_USER@$HOST")
    TARGET_DESC="$SSH_USER@$HOST"
  fi
  SSH=(ssh -o BatchMode=yes "${SSH_I[@]:1}")
}
rx() {  # run the script on stdin on the target: as root for the deploy, as the login user for --check
  if [ "$LOCAL" = 1 ]; then bash -s; else "${SSH[@]}" "${SUDO}bash -s"; fi
}
compose() {  # script [args...]: the script with its arguments, and ownlib's source, in front
  local script=$1; shift
  printf 'set -- %s\n' "$(printf '%q ' ${@+"$@"})"
  printf "OWNLIB_SRC=\$(cat <<'__SHIMPAKU_OWNLIB__'\n"
  cat "$HERE/remote/ownlib.py"
  printf '__SHIMPAKU_OWNLIB__\n)\n'
  printf "EXTRA_FACTS=\$(cat <<'__SHIMPAKU_FACTS__'\n%s\n__SHIMPAKU_FACTS__\n)\n" "${EXTRA_FACTS:-}"
  cat "$script"
}
sh_on_target() {  # a short command string, as root
  if [ "$LOCAL" = 1 ]; then bash -c "$1"; else "${SSH[@]}" "${SUDO}sh -c $(printf '%q' "$1")"; fi
}

openssh_version_ok() {  # SSH_ASKPASS_REQUIRE needs OpenSSH 8.4+
  local v; v=$(ssh -V 2>&1 | sed -n 's/^OpenSSH_\([0-9]*\)\.\([0-9]*\).*/\1 \2/p')
  [ -n "$v" ] || return 1
  local maj min; read -r maj min <<<"$v"
  [ "$maj" -gt 8 ] || { [ "$maj" -eq 8 ] && [ "$min" -ge 4 ]; }
}

install_key_with_password() {  # user: put $KEY.pub in that user's authorized_keys, using their password
  local user=$1 tmpd="" writer="" pw="" rc=0
  # shellcheck disable=SC2016  # runs in the host's shell, reading the key from stdin
  local remote_cmd='umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; k=$(cat); grep -qxF "$k" ~/.ssh/authorized_keys || printf "%s\n" "$k" >> ~/.ssh/authorized_keys'
  # shellcheck disable=SC2054  # the comma is ssh's list separator
  local opts=(-o ConnectTimeout=15 -o UserKnownHostsFile="$KNOWN" -o StrictHostKeyChecking=accept-new -p "$PORT"
              -o PubkeyAuthentication=no -o PreferredAuthentications=keyboard-interactive,password -o NumberOfPasswordPrompts=1)
  have_tty || die "no terminal to read the password from: pass --key FILE (a key already authorized on the host)" 64
  echo "   installing a deploy key for $user@$HOST (the password is used for this one login only)"
  if openssh_version_ok; then
    printf '%s@%s password (not echoed, not stored): ' "$user" "$HOST" >/dev/tty
    IFS= read -rs pw </dev/tty || true
    printf '\n' >/dev/tty
    [ -n "$pw" ] || die "empty password" 5
    # The password reaches ssh through SSH_ASKPASS: a 0700 helper that `cat`s a FIFO in a 0700
    # directory. The FIFO holds nothing on disk; a background builtin `printf` writes the password
    # into it once ssh's helper opens it. No argv, no environment variable, no file carries it.
    tmpd=$(mktemp -d "${TMPDIR:-/tmp}/shimpaku-own.XXXXXX")
    chmod 700 "$tmpd"
    mkfifo -m 600 "$tmpd/pw"
    printf '#!/bin/sh\nexec cat %s\n' "$(printf '%q' "$tmpd/pw")" > "$tmpd/askpass"
    chmod 700 "$tmpd/askpass"
    ( printf '%s\n' "$pw" > "$tmpd/pw" ) &
    writer=$!
    pw=""
    # shellcheck disable=SC2029
    env SSH_ASKPASS="$tmpd/askpass" SSH_ASKPASS_REQUIRE=force DISPLAY="${DISPLAY:-none}" \
      ssh "${opts[@]}" "$user@$HOST" "$remote_cmd" < "$KEY.pub" || rc=$?
    kill "$writer" 2>/dev/null || true
    wait "$writer" 2>/dev/null || true
    rm -rf "$tmpd"
  else
    echo "   (OpenSSH older than 8.4 here: ssh asks for the password itself)"
    # shellcheck disable=SC2029
    ssh "${opts[@]}" "$user@$HOST" "$remote_cmd" < "$KEY.pub" || rc=$?
  fi
  if [ "$rc" != 0 ]; then
    if [ "$user" = root ]; then
      die "could not log in as root with that password. Debian and Ubuntu refuse root password logins by default (PermitRootLogin prohibit-password): re-run with --user <your sudo user>, or --key FILE" 2
    fi
    die "could not log in as $user with that password" 2
  fi
}

connect() {
  [ "$LOCAL" = 1 ] && { TARGET_DESC="this machine ($(hostname))"; return; }
  if [ -n "$ALIAS" ]; then
    ssh_cmd
  else
    if [ "$KEY_GIVEN" = 0 ]; then
      KEY=$HOME/.ssh/shimpaku-own-$HOST
      if [ ! -f "$KEY" ]; then
        mkdir -p "$HOME/.ssh"; chmod 700 "$HOME/.ssh"
        ssh-keygen -q -t ed25519 -N '' -C "shimpaku-own deploy key" -f "$KEY"
        echo "   new deploy key $KEY"
      fi
    fi
    [ -f "$KEY" ] || die "no key file $KEY" 64
    ssh_cmd
    local err
    if ! err=$("${SSH[@]}" true 2>&1); then
      case $err in
        *"Permission denied"*) ;;
        *) die "cannot reach $SSH_USER@$HOST:$PORT over SSH: ${err##*: }" 2 ;;
      esac
      [ "$KEY_GIVEN" = 1 ] && die "the host does not accept $KEY for $SSH_USER" 2
      install_key_with_password "$SSH_USER"
      "${SSH[@]}" true || die "the key was installed but does not log in" 2
    fi
  fi
  local uid; uid=$("${SSH[@]}" 'id -u') || die "ssh to $TARGET_DESC failed" 2
  if [ "$uid" != 0 ]; then
    if "${SSH[@]}" 'sudo -n true' 2>/dev/null; then
      SUDO="sudo -n "
    elif [ "$CHECK" = 0 ] && [ -z "$ALIAS" ] && have_tty; then
      # one interactive sudo (its password goes to the remote sudo prompt, never through us) to
      # authorize the same deploy key for root; every later step then logs in as root
      echo "   $SSH_USER has no passwordless sudo: authorizing the deploy key for root (sudo asks on the host)"
      local pub; pub=$(cat "$KEY.pub")
      "${SSH_I[@]}" -t "sudo sh -c $(printf '%q' "umask 077; mkdir -p /root/.ssh; touch /root/.ssh/authorized_keys; grep -qxF '$pub' /root/.ssh/authorized_keys || echo '$pub' >> /root/.ssh/authorized_keys")" </dev/tty \
        || die "sudo on the host failed" 2
      ROOT_KEY_ADDED=1
      SSH_USER=root; ssh_cmd
      "${SSH[@]}" true || die "root login with the key is refused (PermitRootLogin no?): give $SSH_USER passwordless sudo, or log in as root" 2
    fi
  fi
}

# ---- local facts (images on this computer) and pins ----------------------------------------------
file_size() { wc -c < "$1" | tr -d ' '; }   # wc -c on a regular file is an fstat (BSD and GNU)
canonical_image() {  # kind path -> vJunos-<kind>-<version>.qcow2 (the name lab-gen.py looks for)
  local v; v=$(basename "$2" | grep -oE '[0-9]+\.[0-9]+R[0-9]+(-S[0-9]+)?\.[0-9]+' | head -1 || true)
  printf 'vJunos-%s-%s.qcow2' "$1" "${v:-own}"
}
HOST_IMG_ROUTER="" HOST_IMG_SWITCH="" LOCAL_IMG_ROUTER="" LOCAL_IMG_SWITCH="" EXTRA_FACTS=""
classify_images() {
  local kind p copy=0 magic
  for kind in router switch; do
    if [ $kind = router ]; then p=$IMG_ROUTER; else p=$IMG_SWITCH; fi
    [ -n "$p" ] || continue
    case $p in \~/*) p=$HOME/${p#\~/} ;; esac
    if [ "$LOCAL" = 0 ] && [ -f "$p" ]; then
      magic=$(head -c 4 "$p" | od -An -tx1 | tr -d ' \n')
      EXTRA_FACTS="${EXTRA_FACTS}image_${kind}_path=$p
image_${kind}_where=local file, copied to the host
image_${kind}_state=present
image_${kind}_size=$(file_size "$p")
image_${kind}_format=$([ "$magic" = 514649fb ] && echo qcow2 || echo not-qcow2)
"
      copy=$((copy + $(file_size "$p")))
      if [ $kind = router ]; then LOCAL_IMG_ROUTER=$p; else LOCAL_IMG_SWITCH=$p; fi
    else
      if [ $kind = router ]; then HOST_IMG_ROUTER=$p; else HOST_IMG_SWITCH=$p; fi
    fi
  done
  EXTRA_FACTS="${EXTRA_FACTS}image_copy_bytes=$copy
"
}
pin_published() {  # repo-url commit -> yes | no | ?<why>
  local slug code
  slug=$(printf '%s' "$1" | sed -E 's#^https://github.com/##; s#\.git$##')
  command -v curl >/dev/null || { echo "?curl missing"; return; }
  code=$(curl -sS -o /dev/null -m 15 -w '%{http_code}' "https://api.github.com/repos/$slug/commits/$2" 2>/dev/null || true)
  case $code in 200) echo yes ;; 404|422) echo no ;; *) echo "?http ${code:-none}" ;; esac
}
pins_fact() {
  local a b
  a=$(pin_published "$SHIMPAKU_REPO" "$SHIMPAKU_COMMIT"); b=$(pin_published "$LAB_REPO" "$LAB_COMMIT")
  if [ "$a" = yes ] && [ "$b" = yes ]; then echo yes
  elif [ "$a" = no ]; then echo "no shimpaku ${SHIMPAKU_COMMIT:0:12}"
  elif [ "$b" = no ]; then echo "no juniper-7-day-sprint ${LAB_COMMIT:0:12}"
  else echo "${a#?} ${b#?}"; fi
}

preflight() {  # -> exit code 0/1/2
  local args=(--profile "$PROFILE" --operator "$OP" --data-dir "$DATA" --transport "$([ "$LOCAL" = 1 ] && echo local || echo ssh)")
  [ -n "$HOST_IMG_ROUTER" ] && args+=(--router-image "$HOST_IMG_ROUTER")
  [ -n "$HOST_IMG_SWITCH" ] && args+=(--switch-image "$HOST_IMG_SWITCH")
  [ "$ALLOW_VM" = 1 ] && args+=(--allow-vm)
  [ "$NESTED" = 1 ] && args+=(--enable-nested)
  [ "$JSON" = 1 ] && args+=(--json)
  EXTRA_FACTS="${EXTRA_FACTS}pins_published=$(pins_fact)"
  local rc=0
  compose "$HERE/remote/preflight.sh" "${args[@]}" | rx || rc=$?
  return "$rc"
}

# ---- bundle, stages, images --------------------------------------------------------------------------
STATE=/var/lib/shimpaku-own
ship_bundle() {
  say "shipping the installer bundle to $STATE/bundle"
  local tarflags=""
  tar --version 2>/dev/null | grep -q bsdtar && tarflags="--no-mac-metadata --no-xattrs"
  # --no-same-owner: tar run by root keeps the archive's owner (this computer's uid, which may be a user on
  # the host) and that user could then edit scripts root runs next; extracted, everything is root's.
  # PINS travels with the commit chosen above, so the host clones what this copy is
  local pins; pins=$(mktemp -d "${TMPDIR:-/tmp}/shimpaku-own-pins.XXXXXX")
  sed "s/^SHIMPAKU_COMMIT=.*/SHIMPAKU_COMMIT=$SHIMPAKU_COMMIT/" "$HERE/PINS" > "$pins/PINS"
  # shellcheck disable=SC2086
  (COPYFILE_DISABLE=1 tar $tarflags --exclude __pycache__ -cf - -C "$pins" PINS -C "$HERE" remote configs) |
    if [ "$LOCAL" = 1 ]; then
      bash -c "install -d -m 0755 $STATE && rm -rf $STATE/bundle.new && mkdir -p $STATE/bundle.new && tar --no-same-owner -C $STATE/bundle.new -xf - && rm -rf $STATE/bundle && mv $STATE/bundle.new $STATE/bundle && chmod -R a+rX,go-w $STATE/bundle"
    else
      "${SSH[@]}" "${SUDO}sh -c 'install -d -m 0755 $STATE && rm -rf $STATE/bundle.new && mkdir -p $STATE/bundle.new && tar --no-same-owner -C $STATE/bundle.new -xf - 2>/dev/null && rm -rf $STATE/bundle && mv $STATE/bundle.new $STATE/bundle && chmod -R a+rX,go-w $STATE/bundle'"
    fi
  rm -rf "$pins"
}
stage_args() {
  STAGE_ARGS=(--profile "$PROFILE" --operator "$OP" --data-dir "$DATA" --bind "$BIND")
  [ -n "$HOST_IMG_ROUTER" ] && STAGE_ARGS+=(--router-image "$HOST_IMG_ROUTER")
  [ -n "$HOST_IMG_SWITCH" ] && STAGE_ARGS+=(--switch-image "$HOST_IMG_SWITCH")
  [ "$NESTED" = 1 ] && STAGE_ARGS+=(--enable-nested)
  [ "$SELFTEST" = 1 ] && STAGE_ARGS+=(--selftest)
  return 0
}
run_stage() {  # name [extra args]
  local rc=0
  stage_args
  printf 'exec bash %s/bundle/remote/deploy.sh --stage %s %s\n' "$STATE" "$1" "$(shift; printf '%q ' "${STAGE_ARGS[@]}" ${@+"$@"})" | rx || rc=$?
  case $rc in
    0) ;;
    4) echo; echo "Reboot the host so nested KVM takes effect, then run the same command again (it resumes)."; exit 4 ;;
    *) echo; echo "stage $1 failed (exit $rc). Fix the cause and re-run the same command: every step resumes."; exit 3 ;;
  esac
}
UPLOAD_ARGS=()
upload_images() {
  local kind p name dst size have
  for kind in router switch; do
    if [ $kind = router ]; then p=$LOCAL_IMG_ROUTER; else p=$LOCAL_IMG_SWITCH; fi
    [ -n "$p" ] || continue
    name=$(canonical_image $kind "$p"); dst=$DATA/images/$name; size=$(file_size "$p")
    UPLOAD_ARGS+=(--upload "$kind=$name:$size")
    have=$(sh_on_target "stat -c %s $dst 2>/dev/null || true")
    if [ "$have" = "$size" ]; then note "$name already on the host"; continue; fi
    say "copying $(basename "$p") ($((size / 1048576)) MiB) to $dst"
    sh_on_target "python3 $STATE/bundle/remote/ownlib.py manifest-add $STATE/manifest.json image $dst"
    # written by the operator user, who owns the image dir: root never writes through what is there
    if [ "$LOCAL" = 1 ]; then
      # shellcheck disable=SC2016  # $1 is sh -c's argument
      runuser -u "$OP" -- sh -c 'umask 027; cat > "$1"' _ "$dst.part" < "$p" || die "image copy failed; re-run to retry"
    else
      "${SSH[@]}" "${SUDO}runuser -u $OP -- sh -c 'umask 027; cat > $dst.part'" < "$p" || die "image copy failed; re-run to retry"
    fi
  done
  return 0
}

operator_password() {  # fetched once, printed to the terminal only (fd 3), then deleted on the host
  sh_on_target "cat $STATE/operator-password 2>/dev/null; rm -f $STATE/operator-password" 2>/dev/null || true
}

remove_key() {
  [ "$LOCAL" = 1 ] || [ -n "$ALIAS" ] && { note "--remove-key: no deploy key in this mode"; return; }
  [ -f "$KEY.pub" ] || { note "no $KEY.pub here: nothing to remove"; return; }
  local pub; pub=$(cat "$KEY.pub")
  say "removing the deploy key from the host"
  # root edits files in users' .ssh dirs: its scratch file is its own (mktemp), never <file>.tmp there, and
  # a symlinked authorized_keys is skipped
  printf '%s\n' "$pub" | "${SSH[@]}" "${SUDO}sh -c 'k=\$(cat); t=\$(mktemp); for f in /root/.ssh/authorized_keys /home/*/.ssh/authorized_keys; do [ -f \"\$f\" ] && [ ! -L \"\$f\" ] || continue; grep -qxF \"\$k\" \"\$f\" || continue; grep -vxF \"\$k\" \"\$f\" > \"\$t\" || true; cat \"\$t\" > \"\$f\"; echo \"   removed from \$f\"; done; rm -f \"\$t\"'"
  if [ "$KEY_GIVEN" = 0 ]; then rm -f "$KEY" "$KEY.pub"; note "deleted $KEY here"; fi
}

confirm() {  # typed confirmation of the target, unless --yes
  local want=$1 got
  [ "$YES" = 1 ] && return 0
  have_tty || die "no terminal to confirm on: re-run with --yes to proceed unattended" 5
  ask got "Type '$want' to proceed (anything else aborts)"
  [ "$got" = "$want" ] || die "aborted" 5
}

# ---- dry run -------------------------------------------------------------------------------------------
if [ "$DRY" = 1 ]; then
  command -v python3 >/dev/null || die "--dry-run uses python3 on this computer" 64
  T=${HOST:-<host>}
  base=(--dry-run --os "$DRY_OS" --profile "$PROFILE" --operator "$OP" --bind "$BIND")
  [ "$NESTED" = 1 ] && base+=(--enable-nested)
  rest=("${base[@]}")
  [ "$PROFILE" != fabric ] && rest+=(--upload "router=vJunos-router-$VJUNOS_TESTED.qcow2:0")
  rest+=(--upload "switch=vJunos-switch-$VJUNOS_TESTED.qcow2:0")
  [ "$SELFTEST" = 1 ] && rest+=(--selftest)
  echo "# install.sh --dry-run --profile $PROFILE: every command, in order, for a fresh $DRY_OS host. Nothing runs."
  echo "+ ssh-keygen -q -t ed25519 -N '' -f ~/.ssh/shimpaku-own-$T            (skipped with --key / --ssh-alias / --local)"
  echo "+ ssh -o PubkeyAuthentication=no root@$T 'append the key to ~/.ssh/authorized_keys'   (password read once from /dev/tty, handed over by an SSH_ASKPASS FIFO helper)"
  echo "+ ssh root@$T bash -s < remote/preflight.sh   (read-only checks; any FAIL stops here)"
  echo "+ [you type '$T' to confirm]"
  echo "+ tar PINS remote configs | ssh root@$T 'unpack into $STATE/bundle'"
  echo "+ ssh root@$T bash $STATE/bundle/remote/deploy.sh --stage base:"
  bash "$HERE/remote/deploy.sh" --stage base "${base[@]}" | sed 's/^/    /'
  echo "+ ssh root@$T 'runuser -u $OP -- sh -c \"cat > $DATA/images/<name>.part\"' < <your image file>   (each image given as a local file; skipped when already there)"
  echo "+ ssh root@$T bash $STATE/bundle/remote/deploy.sh --stage rest:"
  bash "$HERE/remote/deploy.sh" --stage rest "${rest[@]}" | sed 's/^/    /'
  echo "+ ssh root@$T 'cat $STATE/operator-password; rm it'   (printed once to your terminal, never to the log)"
  [ "$REMOVE_KEY" = 1 ] && echo "+ ssh root@$T 'drop the deploy key from authorized_keys'; rm ~/.ssh/shimpaku-own-$T"
  exit 0
fi

# ---- uninstall ------------------------------------------------------------------------------------------
if [ "$UNINSTALL" = 1 ]; then
  say "connecting ($LABEL)"
  connect
  [ -n "$SUDO" ] || [ "$LOCAL" = 1 ] || [ "$("${SSH[@]}" 'id -u')" = 0 ] || die "--uninstall needs root on the host" 2
  if ! sh_on_target "test -s $STATE/manifest.json"; then
    echo "no manifest on $TARGET_DESC ($STATE/manifest.json): nothing this installer created is recorded there."
    [ "$REMOVE_KEY" = 1 ] && remove_key
    exit 0
  fi
  ship_bundle >/dev/null
  ua=(); [ "$KEEP_PKGS" = 1 ] && ua=(--keep-packages)
  printf 'exec bash %s/bundle/remote/uninstall.sh --list %s\n' "$STATE" "${ua[*]:-}" | rx
  confirm "$LABEL"
  printf 'exec bash %s/bundle/remote/uninstall.sh --apply %s\n' "$STATE" "${ua[*]:-}" | rx || die "uninstall failed; re-run it (it skips what is already gone)"
  [ "$REMOVE_KEY" = 1 ] && remove_key
  echo; echo "uninstalled from $TARGET_DESC"
  exit 0
fi

# ---- remove the key only ----------------------------------------------------------------------------------
if [ "$REMOVE_KEY" = 1 ] && [ -z "$IMG_ROUTER$IMG_SWITCH" ] && [ "$CHECK" = 0 ]; then
  connect; remove_key; exit 0
fi

# ---- preflight, plan, confirm, deploy -------------------------------------------------------------------
say "connecting ($LABEL)"
ROOT_KEY_ADDED=0
connect
note "target: $TARGET_DESC"
classify_images
say "preflight (read-only) for profile $PROFILE"
rc=0; preflight || rc=$?
if [ "$CHECK" = 1 ]; then exit "$rc"; fi
[ "$rc" = 2 ] && { echo; echo "preflight failed: nothing was changed."; exit 2; }
[ -n "$SUDO" ] || [ "$LOCAL" = 1 ] || [ "$("${SSH[@]}" 'id -u')" = 0 ] || die "the deploy needs root on the host" 2

say "plan"
compose /dev/stdin "$PROFILE" <<'EOS' | rx
python3 -c "$OWNLIB_SRC" plan --profile "$1" | sed 's/^/   /'
EOS
cat <<EOF
   create user $OP (libvirt, linger) and the lab data dir $DATA; install missing packages
   clone shimpaku @ ${SHIMPAKU_COMMIT:0:12} and juniper-7-day-sprint @ ${LAB_COMMIT:0:12} (deploy/own/PINS)
   install uv $UV_VERSION and gnmic $GNMIC_VERSION (sha256-checked) for $OP
   configure the devices over NETCONF (lab boot config + campus baseline + gNMI/RPM), Virga live drills on
   Shimpaku as $OP's user service on $BIND:8780, sign-in required (operator password printed once)
   manifest: $STATE/manifest.json (--uninstall removes exactly what it lists)
EOF
case $BIND in 127.0.0.1|localhost|::1) ;; *) echo "   WARN: --bind $BIND exposes the dashboard beyond loopback; a fresh lab is not hardened for the internet" ;; esac
[ "$rc" = 1 ] && echo "   preflight raised warnings (above)"
confirm "$LABEL"

ship_bundle
run_stage base
[ "$ROOT_KEY_ADDED" = 1 ] || [ "$LOCAL" = 1 ] || [ -n "$ALIAS" ] || [ "$KEY_GIVEN" = 1 ] || \
  sh_on_target "python3 $STATE/bundle/remote/ownlib.py manifest-add $STATE/manifest.json key $(printf '%q' "$(awk '{print $2}' "$KEY.pub" | cut -c1-24)") $(printf '%q' "file=$KEY.pub")" || true
upload_images
run_stage rest ${UPLOAD_ARGS[@]+"${UPLOAD_ARGS[@]}"}

PW=$(operator_password)
say "done"
if [ "$LOCAL" = 1 ]; then
  echo "   dashboard: http://$([ "$BIND" = 127.0.0.1 ] && echo 127.0.0.1 || echo "$BIND"):8780/   (on this machine)"
else
  lf="ssh -L 8780:127.0.0.1:8780"
  [ -n "$ALIAS" ] && echo "   open a tunnel:  $lf $ALIAS" || echo "   open a tunnel:  $lf$([ "$PORT" != 22 ] && echo " -p $PORT") -i $KEY $SSH_USER@$HOST"
  echo "   then browse:    http://127.0.0.1:8780/"
fi
if [ -n "$PW" ]; then
  printf '   sign in as admin with this operator password (shown once, not logged): %s\n' "$PW" >&3
else
  echo "   operator password: already set on an earlier run (reset: sudo -u $OP ~$OP/src/shimpaku/.venv/bin/shimpaku admin set-password)"
fi
PW=""
echo "   uninstall:      $0 $([ "$LOCAL" = 1 ] && echo --local || echo "--host ${HOST:-$ALIAS}") --uninstall"
echo "   manifest:       $STATE/manifest.json on the host;  log: $LOG"
[ "$REMOVE_KEY" = 1 ] && remove_key
exit 0
