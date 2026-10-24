#!/usr/bin/env bash
# Print Cloudflare's current published edge ranges, for the VPS provider firewall (80/443 TCP+UDP
# allow-list). Caddy needs no list: its `trusted_proxies cloudflare` module refreshes itself.
# Run from your machine, diff against the firewall, update by hand. Usage: ops/cloudflare-ips.sh [v4|v6]
set -euo pipefail
for f in ${1:-v4 v6}; do curl -fsS "https://www.cloudflare.com/ips-$f" | sort; echo; done
