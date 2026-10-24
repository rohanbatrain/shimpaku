"""`shimpaku demo …` — the "Missing VLAN" fault-lab fixture used in DEMO.md.

The fixture is a throwaway trunk (`sw1 ge-0/0/5`, wired to nothing) carrying two demo VLANs:
998 stays as a keepalive (Junos refuses a trunk with zero members), 999 is the one the demo
removes and the agent restores. VLAN 999 is also defined on the correlation device (`leaf2`),
which is what guardrail G3 checks before an add is allowed. Every write takes the device lock.

    shimpaku demo seed     # idempotent: create the fixture
    shimpaku demo break    # inject the fault: VLAN 999 removed from the trunk
    shimpaku demo status   # is VLAN 999 a member of the trunk right now?
    shimpaku demo propose  # propose the fix over MCP, as an agent would (needs shimpakud running)
"""

from __future__ import annotations

import json

import typer

from shimpaku import devices
from shimpaku.config import settings
from shimpaku.lock import device_lock

app = typer.Typer(help="Missing-VLAN fault-lab fixture (see DEMO.md).", no_args_is_help=True)

TRUNK_DEV, TRUNK_IF, CORR_DEV = "sw1", "ge-0/0/5", "leaf2"
KEEP, DEMO = 998, 999


def _name(vlan: int) -> str:
    return f"shimpaku-demo-{vlan}"


def _apply(dev_name: str, lines: list[str], why: str) -> None:
    from jnpr.junos.utils.config import Config

    settings.require_device_password()
    dev = devices.open_device(dev_name)
    try:
        with device_lock(dev_name, "shimpaku-demo", why, dev=dev, wait=120, db=settings.lock_db):
            with Config(dev, mode="exclusive") as cu:
                cu.load("\n".join(lines), format="set", merge=True)
                diff = cu.diff()
                if not diff:
                    typer.echo(f"{dev_name}: no change needed")
                    return
                typer.echo(f"{dev_name}:{diff}")
                cu.commit(confirm=3, comment=f"shimpaku demo: {why}")
                cu.commit(comment=f"shimpaku demo: {why} (confirm)")  # confirms the first
        typer.echo(f"{dev_name}: committed and confirmed")
    finally:
        dev.close()


def _trunk_config() -> str:
    settings.require_device_password()
    dev = devices.open_device(TRUNK_DEV)
    try:
        return devices.port_facts(dev, TRUNK_IF).get("config_own", "")
    finally:
        dev.close()


@app.command()
def seed() -> None:
    """Create the fixture (safe to re-run)."""
    _apply(TRUNK_DEV, [
        f"set vlans {_name(KEEP)} vlan-id {KEEP}",
        f"set vlans {_name(DEMO)} vlan-id {DEMO}",
        f"set interfaces {TRUNK_IF} unit 0 family ethernet-switching interface-mode trunk",
        f"set interfaces {TRUNK_IF} unit 0 family ethernet-switching vlan members {_name(KEEP)}",
        f"set interfaces {TRUNK_IF} unit 0 family ethernet-switching vlan members {_name(DEMO)}",
    ], "seed missing-vlan fixture")
    _apply(CORR_DEV, [f"set vlans {_name(DEMO)} vlan-id {DEMO}"], "seed correlation vlan")


@app.command("break")
def break_() -> None:
    """Inject the fault: remove VLAN 999 from the trunk (VLAN 998 keeps the trunk valid)."""
    from jnpr.junos.utils.config import Config

    settings.require_device_password()
    dev = devices.open_device(TRUNK_DEV)
    try:
        with device_lock(TRUNK_DEV, "shimpaku-demo", "inject missing vlan", dev=dev, wait=120,
                         db=settings.lock_db):
            with Config(dev, mode="exclusive") as cu:
                cu.load(f"delete interfaces {TRUNK_IF} unit 0 family ethernet-switching vlan members "
                        f"{_name(DEMO)}", format="set", ignore_warning=True)
                if not cu.diff():
                    typer.echo("already broken: VLAN 999 is not on the trunk")
                    return
                cu.commit(comment="demo fault: vlan 999 missing from trunk")
        typer.echo(f"fault injected: VLAN {DEMO} removed from {TRUNK_DEV} {TRUNK_IF}")
    finally:
        dev.close()


@app.command()
def status() -> None:
    """Show whether VLAN 999 is currently a member of the demo trunk."""
    present = f"vlan members {_name(DEMO)}" in _trunk_config()
    typer.echo(f"VLAN {DEMO} on {TRUNK_DEV} {TRUNK_IF}: {'present' if present else 'MISSING'}")


@app.command()
def propose(url: str = typer.Option(None, help="shimpakud MCP endpoint")) -> None:
    """Propose the fix over MCP — exactly what an external agent would call."""
    import anyio
    from mcp import Client

    from shimpaku.assistant.loop import default_mcp_url

    async def run() -> dict:
        async with Client(url or default_mcp_url()) as c:
            r = await c.call_tool("action_propose_add_vlan_to_trunk", {
                "device": TRUNK_DEV, "ifname": TRUNK_IF, "vlan": DEMO,
                "reason": "VLAN 999 missing from the sw1 trunk; restore it"})
            return r.structured_content or {}

    out = anyio.run(run)
    typer.echo(json.dumps({k: out.get(k) for k in ("id", "status", "tier", "kind")}, indent=2))
    typer.echo("Approve it on the dashboard: Actions → this action → Approve.")
