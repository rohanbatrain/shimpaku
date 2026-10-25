#!/usr/bin/env bash
# Preflight for "Deploy your own" (docs/build/deploy-your-own.md §2). READ-ONLY: it reads /proc,
# /sys, /etc/os-release, `ip`, `ss`, `virsh list`, `nft list tables` (root only) and opens one TCP
# connection each to github.com:443 and pypi.org:443. It writes nothing, installs nothing, starts
# nothing it can avoid (a socket-activated libvirtd may wake up for `virsh list`).
#
# install.sh sends this over ssh stdin (or runs it locally) behind a small header that sets
# OWNLIB_SRC (remote/ownlib.py's source), EXTRA_FACTS (facts measured on the visitor's machine,
# e.g. local image files) and the arguments:
#   --profile full|fabric|campus  --data-dir /home/lab  --operator shimpaku  --transport ssh|local
#   [--router-image HOSTPATH] [--switch-image HOSTPATH] [--json] [--facts] [--allow-vm] [--enable-nested]
set -uo pipefail
export LC_ALL=C
PATH=$PATH:/usr/sbin:/sbin

PROFILE=full DATA=/home/lab OP=shimpaku TRANSPORT=ssh JSON=0 FACTS_ONLY=0 EVAL_FLAGS=""
IMG_ROUTER="" IMG_SWITCH=""
while (($#)); do
  case $1 in
    --profile) PROFILE=$2; shift ;;
    --data-dir) DATA=$2; shift ;;
    --operator) OP=$2; shift ;;
    --transport) TRANSPORT=$2; shift ;;
    --router-image) IMG_ROUTER=$2; shift ;;
    --switch-image) IMG_SWITCH=$2; shift ;;
    --json) JSON=1 ;;
    --facts) FACTS_ONLY=1 ;;
    --allow-vm) EVAL_FLAGS+=" --allow-vm" ;;
    --enable-nested) EVAL_FLAGS+=" --enable-nested" ;;
  esac
  shift
done
MANIFEST=/var/lib/shimpaku-own/manifest.json

fact() { printf '%s=%s\n' "$1" "$2"; }
first() { "$@" 2>/dev/null | head -1; }
join() { paste -sd, -; }
yn() { if "$@" >/dev/null 2>&1; then echo yes; else echo no; fi; }
tcp() { yn timeout 8 bash -c "exec 3<>/dev/tcp/$1/$2"; }

image_facts() {  # kind hostpath
  local kind=$1 p=$2 magic
  [[ -z $p ]] && return
  fact "image_${kind}_path" "$p"
  fact "image_${kind}_where" "on the host"
  if [[ -f $p && -r $p ]]; then
    fact "image_${kind}_state" present
    fact "image_${kind}_size" "$(stat -c %s "$p")"
    magic=$(head -c 4 "$p" | od -An -tx1 | tr -d ' \n')
    if [[ $magic == 514649fb ]]; then fact "image_${kind}_format" qcow2; else fact "image_${kind}_format" not-qcow2; fi
  else
    fact "image_${kind}_state" missing
  fi
}

collect() {
  fact transport "$TRANSPORT"
  fact uid "$(id -u)"
  fact whoami "$(id -un)"
  if [[ $(id -u) != 0 ]]; then
    fact sudo "$(yn sudo -n true)"
  fi
  fact kernel "$(uname -s)"
  fact arch "$(uname -m)"
  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    ( . /etc/os-release; fact os_id "${ID:-}"; fact os_version "${VERSION_ID:-}"; fact os_pretty "${PRETTY_NAME:-}" )
  fi
  fact cpu_virt "$(grep -m1 -ow -E 'vmx|svm' /proc/cpuinfo 2>/dev/null || echo none)"
  fact dev_kvm "$(yn test -e /dev/kvm)"
  local m
  for m in kvm_intel kvm_amd; do
    if [[ -r /sys/module/$m/parameters/nested ]]; then
      fact nested_module "$m"; fact nested "$(cat "/sys/module/$m/parameters/nested")"
    fi
  done
  if command -v systemd-detect-virt >/dev/null; then
    fact virt "$(systemd-detect-virt 2>/dev/null || true)"
  else
    fact virt unknown
  fi
  fact dmi_vendor "$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null)"
  fact dmi_product "$(cat /sys/class/dmi/id/product_name 2>/dev/null)"
  fact mem_available_kib "$(awk '/^MemAvailable:/{print $2}' /proc/meminfo)"
  fact mem_total_kib "$(awk '/^MemTotal:/{print $2}' /proc/meminfo)"
  fact threads "$(nproc)"

  local p=$DATA
  while [[ ! -e $p ]]; do p=$(dirname "$p"); done
  fact disk_path "$p"
  fact disk_free_kib "$(df -Pk "$p" | awk 'NR==2{print $4}')"
  fact data_dir "$DATA"
  fact data_dir_exists "$(yn test -e "$DATA")"

  if command -v virsh >/dev/null; then
    local all running
    if all=$(timeout 15 virsh -c qemu:///system list --all --name 2>/dev/null); then
      fact virsh_access yes
      running=$(timeout 15 virsh -c qemu:///system list --name 2>/dev/null)
      fact lab_domains "$(grep '^lab-' <<<"$all" | join)"
      fact lab_domains_running "$(grep '^lab-' <<<"$running" | join)"
      fact other_vms_running "$(grep -v '^lab-' <<<"$running" | grep -c . )"
      fact lab_networks "$(timeout 15 virsh -c qemu:///system net-list --all --name 2>/dev/null | grep '^lab-' | join)"
    else
      fact virsh_access no
    fi
  fi
  fact lab_links "$(ip -o link show 2>/dev/null | awk -F': ' '{print $2}' | sed 's/@.*//' | grep '^lab-' | join)"
  if [[ $(id -u) == 0 ]] && command -v nft >/dev/null; then
    fact nft_readable yes
    fact nft_tables "$(nft list tables 2>/dev/null | awk '{print $2" "$3}' | join)"
  else
    fact nft_readable no
  fi
  fact routes "$(ip -4 route show table all 2>/dev/null | awk '
      $1 ~ /^(local|broadcast|multicast|anycast)$/ {next}
      $1 ~ /^(unreachable|blackhole|prohibit|throw)$/ {print $2; next}
      {print $1}' | grep -v '^default$' | sort -u | join)"
  if command -v ss >/dev/null; then
    fact port_8780 "$(yn test -n "$(ss -Hltn 'sport = :8780' 2>/dev/null)")"
  fi
  local stacks=()
  { [[ -d /etc/pve ]] || command -v pveversion >/dev/null; } && stacks+=(proxmox)
  pgrep -x 'VBoxHeadless|VirtualBoxVM|VBoxSVC' >/dev/null 2>&1 && stacks+=(virtualbox)
  pgrep -f vmware-vmx >/dev/null 2>&1 && stacks+=(vmware)
  fact hv_stacks "$(IFS=,; echo "${stacks[*]:-}")"

  local pm=none
  for m in apt-get:apt pacman:pacman dnf:dnf; do
    command -v "${m%%:*}" >/dev/null && { pm=${m##*:}; break; }
  done
  fact pkg_mgr "$pm"
  local miss=() t
  for t in virsh qemu-img mkfs.vfat mcopy python3 git curl nft ip tar runuser loginctl; do
    command -v "$t" >/dev/null || miss+=("$t")
  done
  [[ -x /usr/bin/qemu-system-x86_64 ]] || miss+=(qemu-system-x86_64)
  command -v libvirtd >/dev/null || command -v virtqemud >/dev/null || miss+=(libvirtd)
  fact missing_tools "$(IFS=,; echo "${miss[*]:-}")"
  if command -v python3 >/dev/null; then
    fact python3_version "$(python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null)"
  fi
  fact ntp_sync "$(first timedatectl show -p NTPSynchronized --value || echo unknown)"
  fact github "$(tcp github.com 443)"
  fact pypi "$(tcp pypi.org 443)"
  fact operator_exists "$(yn getent passwd "$OP")"
  fact operator_group_exists "$(yn getent group "$OP")"
  fact operator "$OP"
  image_facts router "$IMG_ROUTER"
  image_facts switch "$IMG_SWITCH"
  fact other_images "$(find "$DATA/images" -maxdepth 1 -name 'vJunos-*.qcow2' 2>/dev/null | sort | join)"
  printf '%s\n' "${EXTRA_FACTS:-}"
}

FACTS=$(collect)
if ((FACTS_ONLY)); then printf '%s\n' "$FACTS"; exit 0; fi

if ! command -v python3 >/dev/null || [[ -z ${OWNLIB_SRC:-} ]]; then
  echo "  FAIL  Python  python3 is missing on the host: install python3 (>= 3.11) with the package manager and re-run"
  exit 2
fi
args=(evaluate --profile "$PROFILE" --manifest "$MANIFEST")
((JSON)) && args+=(--json)
[[ -t 1 ]] && args+=(--colour)
# shellcheck disable=SC2206
args+=($EVAL_FLAGS)
printf '%s\n' "$FACTS" | python3 -c "$OWNLIB_SRC" "${args[@]}"
