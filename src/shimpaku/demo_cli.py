"""`shimpaku demo …` — the "Missing VLAN" fault-lab fixture used in DEMO.md.

The fixture is a throwaway trunk (`sw1 ge-0/0/5`, wired to nothing) carrying two demo VLANs:
998 stays as a keepalive (Junos refuses a trunk with zero members), 999 is the one the demo
removes and the agent restores. VLAN 999 is also defined on the correlation device (`leaf2`),
which is what guardrail G3 checks before an add is allowed. `seed` and `break` call the audited
service operations (`service.demo_seed` / `service.demo_break`): each takes the device lock and
writes a hash-chained audit row with the caller's role (here: operator, over `cli`).

    shimpaku demo seed     # idempotent: create the fixture
    shimpaku demo break    # inject the fault: VLAN 999 removed from the trunk
    shimpaku demo status   # is VLAN 999 a member of the trunk right now?
    shimpaku demo propose  # propose the fix over MCP, as an agent would (needs shimpakud running)
"""

from __future__ import annotations

import json

import typer

from shimpaku import devices
from shimpaku.actions import fixture
from shimpaku.config import settings

app = typer.Typer(help="Missing-VLAN fault-lab fixture (see DEMO.md).", no_args_is_help=True)

# One definition of the fixture, shared with the engine's tester gate (actions/fixture.py).
TRUNK_DEV, TRUNK_IF, CORR_DEV = fixture.DEVICE, fixture.IFNAME, fixture.CORR_DEVICE
KEEP, DEMO = fixture.KEEP_VLAN, fixture.VLAN
_name = fixture.vlan_name


def _cli_caller():
    """The person at the lab host's shell: the operator, over the CLI transport."""
    import getpass

    from shimpaku.actions.engine import Caller
    return Caller(kind="cli", name=f"os:{getpass.getuser()}", transport="cli", role="operator")


def _run(op: str) -> dict:
    """Run the audited service operation (the same one the daemon exposes) from the CLI."""
    import asyncio

    from shimpaku import service
    from shimpaku.mcp.audit import AuditLog

    settings.require_device_password()
    if service._audit is None:
        service.set_audit_log(AuditLog(settings.ensure_data() and settings.sqlite_path,
                                       settings.data / "audit.jsonl"))
    out = asyncio.run(service.demo_seed(_cli_caller()) if op == "seed" else service.demo_break(_cli_caller()))
    if isinstance(out.get("error"), dict):
        typer.echo(f"refused: {out['error']['message']}", err=True)
        raise typer.Exit(1)
    return out


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
    for w in _run("seed")["writes"]:
        typer.echo(f"{w['device']}:{w['diff']}\n{w['device']}: committed and confirmed" if w["changed"]
                   else f"{w['device']}: no change needed")


@app.command("break")
def break_() -> None:
    """Inject the fault: remove VLAN 999 from the trunk (VLAN 998 keeps the trunk valid)."""
    if not _run("break")["changed"]:
        typer.echo(f"already broken: VLAN {DEMO} is not on the trunk")
        return
    typer.echo(f"fault injected: VLAN {DEMO} removed from {TRUNK_DEV} {TRUNK_IF}")


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
