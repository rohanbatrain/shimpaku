"""``shimpaku admin set-password``, ``admin invite create|list|revoke``, ``admin purge``.

``set-password``: store the admin's argon2id hash in ``<data>/admin.hash`` (0600).

Wire into the main CLI with ``app.add_typer(admin_app, name="admin")``. The password is read from
a hidden prompt (or stdin with ``--stdin``) and never printed. ``SHIMPAKU_ADMIN_PASSWORD_HASH``,
when set, takes precedence over the file.
"""

from __future__ import annotations

import os
import sys

import typer

from shimpaku.api.auth import admin_hash_file, hash_password
from shimpaku.config import settings

admin_app = typer.Typer(help="Dashboard admin account (auth mode local).", no_args_is_help=True)


@admin_app.command("set-password")
def set_password(
    stdin: bool = typer.Option(False, "--stdin", help="Read the password from stdin instead of a prompt."),
    print_hash: bool = typer.Option(False, "--print-hash", help="Print the hash (for SHIMPAKU_ADMIN_PASSWORD_HASH) instead of writing the file."),
) -> None:
    if stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = typer.prompt("New admin password", hide_input=True, confirmation_prompt=True)
    if len(password) < 12:
        typer.echo("refused: use at least 12 characters", err=True)
        raise typer.Exit(2)
    h = hash_password(password)
    if print_hash:
        typer.echo(h)
        return
    f = admin_hash_file(settings.ensure_data())
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(h + "\n")
    os.chmod(f, 0o600)
    typer.echo(f"admin password set ({f}); restart shimpakud with SHIMPAKU_AUTH_MODE=local")


# ---- tester passes (DEPLOY-DESIGN build step 4) -------------------------------------------------
invite_app = typer.Typer(help="Single-use tester invites.", no_args_is_help=True)
admin_app.add_typer(invite_app, name="invite")


def _leases():
    """The lease manager on the daemon's state.db. Lease cleanup (key deletion, re-seed) is left
    to the running daemon, which picks up a lease marked ending within seconds."""
    from shimpaku import service
    from shimpaku.api.auth import AuthStore
    from shimpaku.api.lease import from_settings
    from shimpaku.mcp.audit import AuditLog

    settings.ensure_data()
    if service._audit is None:
        service.set_audit_log(AuditLog(settings.sqlite_path, settings.data / "audit.jsonl"))
    return from_settings(AuthStore(settings.sqlite_path), service)


def _when(t: float | None) -> str:
    import time as _t
    return _t.strftime("%Y-%m-%d %H:%M UTC", _t.gmtime(t)) if t else "-"


@invite_app.command("create")
def invite_create(
    note: str = typer.Option("", "--note", help="Who it is for (kept 90 days after the lease, never audited)."),
    hours: float = typer.Option(None, "--hours", help="Hours to redeem (default SHIMPAKU_INVITE_TTL_HOURS, 72)."),
) -> None:
    from shimpaku.api.app import invite_link

    if not settings.public_url:
        typer.echo("refused: set SHIMPAKU_PUBLIC_URL (the fixed https base the link is built from)", err=True)
        raise typer.Exit(2)
    inv = _leases().create_invite(note, "cli", ttl_s=hours * 3600 if hours else None)
    typer.echo(f"invite {inv['id']} (expires {_when(inv['expires'])}; shown once):")
    typer.echo(invite_link(inv["token"]))


@invite_app.command("list")
def invite_list() -> None:
    for i in _leases().list_invites():
        typer.echo(f"{i['id']}  {i['status']:<9} expires {_when(i['expires'])}  lease {i['lease_id'] or '-'}  "
                   f"{i['note'] if i['note'] is not None else '(purged)'}")


@invite_app.command("revoke")
def invite_revoke(invite_id: str) -> None:
    if not _leases().revoke_invite(invite_id, "cli"):
        typer.echo("no such open invite", err=True)
        raise typer.Exit(1)
    typer.echo(f"revoked {invite_id} (its lease, if any, ends within seconds)")


@admin_app.command("purge")
def purge() -> None:
    """Delete tester identity data (invite notes, display names) past the retention period."""
    out = _leases().purge()
    typer.echo(f"purged: {out['invites']} invite notes, {out['leases']} display names")


if __name__ == "__main__":
    admin_app()
