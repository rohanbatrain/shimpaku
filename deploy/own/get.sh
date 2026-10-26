#!/usr/bin/env bash
# Shimpaku "Deploy your own": the one-line bootstrap.
#
#   curl -fsSL https://<landing-domain>/install.sh | bash                     (asks for what it needs)
#   curl -fsSL https://<landing-domain>/install.sh | bash -s -- --host 192.0.2.10 --profile fabric
#   curl -fsSL https://<landing-domain>/install.sh | less                     (read it first)
#
# It downloads the Shimpaku source at one pinned commit from GitHub into
# ~/.local/share/shimpaku-own/src-<ref> (reused when already there) and runs
# deploy/own/install.sh from it with your arguments. That script does the work: read-only
# preflight, a plan, your typed confirmation, then the build. See deploy/own/README.md.
#
# Everything is inside main(), called on the last line: a download cut short runs nothing.

# The owner's site build replaces the placeholder with the published commit this page was tested with.
SHIMPAKU_REF="${SHIMPAKU_REF:-__SHIMPAKU_PIN__}"
SHIMPAKU_TARBALL_BASE="https://codeload.github.com/rohanbatrain/shimpaku/tar.gz"

main() {
  set -euo pipefail
  umask 077

  warn() { printf 'WARN: %s\n' "$*" >&2; }
  die() { printf 'error: %s\n' "$*" >&2; exit 64; }

  # bash 3.2 (macOS's /bin/bash) is enough: install.sh is written for it.
  [ "${BASH_VERSINFO[0]:-0}" -ge 3 ] || die "bash 3.2 or newer is needed"
  local t
  for t in curl tar uname mktemp; do command -v "$t" >/dev/null 2>&1 || die "$t is needed and missing"; done
  command -v ssh >/dev/null 2>&1 || warn "ssh is missing: only --local (deploying on this machine) will work"
  case $(uname -s) in Darwin|Linux) ;; *) warn "untested on $(uname -s); macOS and Linux are supported" ;; esac

  local ref=$SHIMPAKU_REF
  local placeholder="__SHIMPAKU"; placeholder="${placeholder}_PIN__"
  if [ "$ref" = "$placeholder" ]; then
    warn "this copy of the bootstrap has no pinned commit; using the 'public' branch (not reproducible)"
    ref=public
  fi
  case $ref in *[!A-Za-z0-9._-]*|"") die "SHIMPAKU_REF '$ref' is not a commit or branch name" ;; esac

  local base=${XDG_DATA_HOME:-$HOME/.local/share}/shimpaku-own
  local dest=$base/src-$ref url=$SHIMPAKU_TARBALL_BASE/$ref pinned=0
  # only a full commit id is immutable; a short hex string may be a branch or tag name: download it again
  case ${#ref}:$ref in 40:*[!0-9a-f]*) ;; 40:*) pinned=1 ;; esac

  if [ "$pinned" = 1 ] && [ -f "$dest/deploy/own/install.sh" ]; then
    echo "using Shimpaku $ref already in $dest"
  else
    echo "downloading Shimpaku $ref"
    echo "   from $url"
    echo "   into $dest"
    mkdir -p "$base"
    local tmp; tmp=$(mktemp -d "$base/.get.XXXXXX")
    # shellcheck disable=SC2064  # expand now: the trap must remove this run's directory
    trap "rm -rf '$tmp'" EXIT
    curl -fsSL --proto '=https' --tlsv1.2 --retry 2 -o "$tmp/src.tgz" "$url" || die "download failed: $url"
    mkdir "$tmp/src"
    tar -xzf "$tmp/src.tgz" -C "$tmp/src" --strip-components 1 || die "the download is not a Shimpaku source tarball"
    [ -f "$tmp/src/deploy/own/install.sh" ] || die "the downloaded source has no deploy/own/install.sh (ref $ref too old?)"
    rm -rf "$dest"
    mv "$tmp/src" "$dest"
    rm -rf "$tmp"   # exec below replaces this shell, so the EXIT trap would never run
    trap - EXIT
  fi

  # stdin is this script when piped from curl: install.sh asks on /dev/tty, never on stdin
  # SHIMPAKU_REF tells install.sh which commit the host should clone: the one just downloaded
  SHIMPAKU_REF=$ref exec bash "$dest/deploy/own/install.sh" "$@" </dev/null
}

main "$@"
