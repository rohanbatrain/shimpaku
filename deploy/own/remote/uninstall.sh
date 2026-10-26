#!/usr/bin/env bash
# Remove exactly what "Deploy your own" created (D6): the manifest at
# /var/lib/shimpaku-own/manifest.json, newest entry first. Nothing that is not in it is touched.
#
#   uninstall.sh --list                    what would go, in order (changes nothing)
#   uninstall.sh --apply [--keep-packages] remove it
#
# The deploy key in authorized_keys is not handled here: install.sh removes it last
# (--remove-key), because it is the way in.
set -euo pipefail
export LC_ALL=C
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
STATE=/var/lib/shimpaku-own
MANIFEST=$STATE/manifest.json
MODE="" KEEP_PKGS=0
while (($#)); do
  case $1 in
    --list) MODE=list ;;
    --apply) MODE=apply ;;
    --keep-packages) KEEP_PKGS=1 ;;
    *) echo "uninstall.sh: unknown option $1" >&2; exit 64 ;;
  esac
  shift
done
[[ -n $MODE ]] || { echo "uninstall.sh --list | --apply" >&2; exit 64; }
if [[ ! -s $MANIFEST ]]; then echo "no manifest at $MANIFEST: nothing recorded, nothing to remove"; exit 0; fi
[[ $(id -u) == 0 ]] || { echo "uninstall.sh: run as root" >&2; exit 1; }

entries=$(python3 "$HERE/ownlib.py" manifest-list "$MANIFEST" --reverse)
if [[ $MODE == list ]]; then
  echo "will remove, in this order:"
  while IFS=$'\t' read -r kind name _; do
    case $kind in
      package) ((KEEP_PKGS)) && continue ;;
      key) continue ;;
    esac
    printf '  %-13s %s\n' "$kind" "$name"
  done <<<"$entries"
  ((KEEP_PKGS)) || echo "  (packages too; --keep-packages leaves them installed)"
  exit 0
fi

run() { printf '+ %s\n' "$*"; "$@" || echo "  (already gone or failed: continuing)"; }
safe_path() {  # never rm -rf something short or outside the places the installer writes
  case $1/ in
    */../*|*/./*|*//*) echo "refusing to remove $1 (not a plain absolute path)" >&2; return 1 ;;
  esac
  case $1 in
    /home/*/*|/home/lab|/var/lib/shimpaku-own/*|/etc/modprobe.d/kvm-nested.conf) return 0 ;;
  esac
  echo "refusing to remove $1 (outside the installer's paths)" >&2; return 1
}
user_systemctl() {  # user command...
  local u=$1 uid; shift
  uid=$(id -u "$u" 2>/dev/null) || return 0
  runuser -u "$u" -- env XDG_RUNTIME_DIR="/run/user/$uid" systemctl --user "$@" 2>/dev/null || true
}

pkgs=() pm="" had_nested=0
while IFS=$'\t' read -r kind name extra; do
  case $kind in
    user-unit)
      op=$(python3 "$HERE/ownlib.py" manifest-list "$MANIFEST" | awk -F'\t' '$1=="user"{print $2; exit}')
      [[ -n $op ]] && { echo "+ [as $op] systemctl --user disable --now $name"; user_systemctl "$op" disable --now "$name.service"; } ;;
    domain)
      if virsh -c qemu:///system dominfo "$name" >/dev/null 2>&1; then
        [[ $(virsh -c qemu:///system domstate "$name" 2>/dev/null) == running ]] && run virsh -c qemu:///system destroy "$name"
        run virsh -c qemu:///system undefine --snapshots-metadata "$name"
      fi ;;
    bridge) ip link show "$name" >/dev/null 2>&1 && run ip link del "$name" ;;
    nft-table) nft list table "$name" >/dev/null 2>&1 && run nft delete table "$name" ;;  # nft joins its argv
    image|file)
      [[ $name == /etc/modprobe.d/kvm-nested.conf ]] && had_nested=1
      [[ -e $name ]] && safe_path "$name" && run rm -f "$name" ;;
    dir|repo) [[ -e $name ]] && safe_path "$name" && run rm -rf --one-file-system "$name" ;;
    linger) id "$name" >/dev/null 2>&1 && run loginctl disable-linger "$name" ;;
    group-member)
      u=${name%%:*} g=${name#*:}
      id "$u" >/dev/null 2>&1 && getent group "$g" >/dev/null && run gpasswd -d "$u" "$g" ;;
    user)
      if id "$name" >/dev/null 2>&1; then
        loginctl terminate-user "$name" 2>/dev/null || true
        pkill -u "$name" 2>/dev/null || true; sleep 2
        run userdel --remove "$name"
      fi ;;
    service) run systemctl disable --now "$name" ;;
    package)
      pkgs+=("$name"); pm=$(sed -n 's/.*"manager": "\([a-z]*\)".*/\1/p' <<<"$extra") ;;
    key) : ;;
    *) echo "unknown manifest entry $kind $name: left alone" ;;
  esac
done <<<"$entries"

if ((${#pkgs[@]})) && ((!KEEP_PKGS)); then
  case $pm in
    apt) run apt-get remove -y -q "${pkgs[@]}" ;;
    pacman) run pacman -R --noconfirm "${pkgs[@]}" ;;
    dnf) run dnf remove -y -q "${pkgs[@]}" ;;
  esac
fi
((had_nested)) && echo "note: nested KVM stays on until the next reboot or module reload"
run rm -rf "$STATE"
echo "removed everything the manifest listed, then the manifest"
