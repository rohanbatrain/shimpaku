"""deploy/vps/Caddyfile: the edge's security headers and the status host's allow-list, read as text.

When a `caddy` binary is on PATH the file (minus the two plugin directives) must also adapt.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

CADDYFILE = Path(__file__).resolve().parents[2] / "deploy" / "vps" / "Caddyfile"
TEXT = CADDYFILE.read_text()


def block(head: str) -> str:
    """The text of the top-level site block that starts with `head`."""
    i = TEXT.index("\n" + head + " {") + 1
    depth, j = 0, i + len(head)  # the head itself, {$ENV}, has braces
    while True:
        c = TEXT[j]
        depth += c == "{"
        depth -= c == "}"
        j += 1
        if c == "}" and depth == 0:
            return TEXT[i:j]


def test_the_shared_referrer_policy_is_only_a_default():
    common = TEXT[TEXT.index("(common) {"):TEXT.index("(upstream_opts)")]
    # the block deletes Server, so it is deferred: a plain set would overwrite no-referrer from the app
    assert "-Server" in common and "?Referrer-Policy same-origin" in common
    assert not re.search(r"^\s*Referrer-Policy same-origin", common, re.M)


def test_invite_and_request_status_pages_send_no_referrer():
    app = block("{$SHIMPAKU_APP_HOSTS}")
    assert re.search(r"@token_pages path /start /request/status\n\s*header @token_pages Referrer-Policy no-referrer", app)


def test_the_landing_page_has_a_csp_and_cannot_be_framed():
    site = block("{$SHIMPAKU_SITE_HOSTS}")
    csp = re.search(r'Content-Security-Policy "([^"]+)"', site).group(1)
    for d in ("default-src 'self'", "script-src 'self' https://static.cloudflareinsights.com", "object-src 'none'", "frame-ancestors 'none'", "base-uri 'none'"):
        assert d in csp
    assert "unsafe-eval" not in csp and "X-Frame-Options DENY" in site


def test_the_status_host_root_is_get_only():
    status = block("{$SHIMPAKU_STATUS_HOSTS}")
    assert re.search(r"@root \{\s*method GET HEAD\s*path /\s*\}\s*handle @root \{", status)
    assert "handle / {" not in status


@pytest.mark.skipif(not shutil.which("caddy"), reason="caddy not installed")
def test_adapts(tmp_path):
    s = re.sub(r"^\{\n.*?^\}\n", "{\n\tadmin off\n}\n", TEXT, count=1, flags=re.S | re.M)  # trusted_proxies cloudflare
    s = re.sub(r"\trate_limit @\w+ \{\n.*?\n\t\}\n", "", s, flags=re.S)                         # the rate_limit plugin
    (tmp_path / "Caddyfile").write_text(s)
    env = {**os.environ, "SHIMPAKU_SITE_HOSTS": "a.example", "SHIMPAKU_APP_HOSTS": "a-app.example",
           "SHIMPAKU_STATUS_HOSTS": "a-status.example", "SHIMPAKU_LAB_UPSTREAM": "127.0.0.1:9"}
    p = subprocess.run(["caddy", "adapt", "--config", str(tmp_path / "Caddyfile")], capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr[-500:]
