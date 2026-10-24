"""Raw `show` reads through Juniper's junos-mcp-server, behind our allowlist and our audit.

Juniper's server (github.com/Juniper/junos-mcp-server, tested by R2 §7 under ~/aiops-r2) is CLI
passthrough with a plain `commit` and no audit (R7 §2.1), so agents never see it directly. We
spawn it over stdio through `jmcp_readonly.py`, which removes its write tools before it serves,
and we only ever call `execute_junos_command` with a command that passed `check_command`.
If the Juniper server is not installed, the same allowlisted command runs on our PyEZ pool and
the result says `path: "pyez"`.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from shimpaku import devices
from shimpaku.config import settings

# Defaults match the lab host; the orchestrator may lift these into settings (see report).
JMCP_DIR = Path.home() / "aiops-r2/aiops-junos-mcp"
JMCP_PYTHON = Path.home() / "aiops-r2/aiops-mcp-venv/bin/python"
WRAPPER = Path(__file__).with_name("jmcp_readonly.py")

ALLOW_PREFIXES = (
    "show interfaces", "show lldp neighbors", "show ospf neighbor", "show ospf interface",
    "show ospf database", "show bgp summary", "show bgp neighbor", "show bfd session",
    "show route", "show system commit", "show system uptime", "show system alarms",
    "show chassis alarms", "show chassis hardware", "show version", "show ethernet-switching table",
    "show ethernet-switching interface", "show vlans", "show evpn database", "show evpn instance",
    "show arp", "show lacp interfaces", "show services rpm probe-results", "show dhcp server binding",
)
DENY_WORDS = ("configuration", "log", "file", "security", "user", "login", "pki", "ssh", "secret", "key")
CMD_RE = re.compile(r"^show [a-z0-9 ./:_-]{1,110}$")


def check_command(command: str) -> str | None:
    """None if allowed, else the reason it is refused. No pipes, no `;`, no newlines."""
    if not isinstance(command, str):
        return "command must be a string"
    c = " ".join(command.strip().split())
    if c != command.strip() or not CMD_RE.match(c):
        return "only plain `show …` commands (lowercase, no pipes, no `;`, no newlines) are allowed"
    words = set(c.split())
    if words & set(DENY_WORDS):
        return f"`{c}` touches a denied area ({', '.join(sorted(words & set(DENY_WORDS)))})"
    if not any(c == p or c.startswith(p + " ") for p in ALLOW_PREFIXES):
        return f"`{c}` is not on the show allowlist (lab://policy)"
    return None


def _devices_file() -> Path:
    p = settings.ensure_data() / "jmcp-devices.json"
    inv = devices.inventory()
    body = {n.name: {"ip": n.mgmt_ip, "port": 22, "username": settings.device_user,
                     "auth": {"type": "password", "password": settings.device_password}}
            for n in inv.nodes.values()}
    data = json.dumps(body, indent=1)
    if not p.exists() or p.read_text() != data:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(data)
    return p


def available() -> bool:
    return JMCP_PYTHON.exists() and (JMCP_DIR / "jmcp.py").exists()


async def run_show(device: str, command: str) -> dict:
    why = check_command(command)
    if why:
        return {"error": {"code": "not_allowed", "message": why}}
    devices.node(device)  # raises UnknownDevice
    command = " ".join(command.split())
    if not available():
        import asyncio
        out = await asyncio.to_thread(devices.raw_show, device, command)
        return {"device": device, "command": command, "path": "pyez", "output": out[:20000]}
    from mcp.client import Client
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(command=str(JMCP_PYTHON),
                                   args=[str(WRAPPER), str(JMCP_DIR), "-f", str(_devices_file())],
                                   cwd=str(JMCP_DIR))
    with open(settings.ensure_data() / "jmcp.log", "a") as errlog:  # its INFO logs, off our stdio
        async with Client(stdio_client(params, errlog=errlog), mode="legacy") as c:
            tools = {t.name for t in (await c.list_tools()).tools}
            if tools & {"load_and_commit_config", "render_and_apply_j2_template"}:
                return {"error": {"code": "unsafe_backend", "message": "junos-mcp-server exposes write tools"}}
            r = await c.call_tool("execute_junos_command", {"router_name": device, "command": command})
    text = "".join(getattr(b, "text", "") for b in r.content)
    return {"device": device, "command": command, "path": "junos-mcp-server", "backend_tools": sorted(tools),
            "output": text[:20000], "backend_error": bool(r.is_error)}
