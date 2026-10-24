"""``shimpaku admin set-password``, ``admin totp enable|disable|status``, ``admin invite create|list|revoke``, ``admin requests
list|approve|decline``, ``admin purge``.

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


# ---- operator second factor (TOTP) -------------------------------------------------------------
totp_app = typer.Typer(help="Operator two-factor sign-in (TOTP + one-time recovery codes).", no_args_is_help=True)
admin_app.add_typer(totp_app, name="totp")


def _totp():
    from shimpaku.api.totp import TotpStore, totp_file

    return TotpStore(totp_file(settings.ensure_data()))


@totp_app.command("enable")
def totp_enable(force: bool = typer.Option(False, "--force", help="Replace an existing secret and recovery codes.")) -> None:
    """Create the TOTP secret and 8 recovery codes; print them once (to this terminal only)."""
    from shimpaku.api.totp import terminal_qr

    store = _totp()
    if store.enabled and not force:
        typer.echo("refused: TOTP is already enabled (use --force to replace the secret and recovery codes)", err=True)
        raise typer.Exit(2)
    out = store.enable()
    typer.echo("Add this to your authenticator app (shown once; it is not logged):\n")
    qr = terminal_qr(out["uri"])
    if qr:
        typer.echo(qr)
    else:
        typer.echo("(no QR library installed (qrcode or segno): paste the URI or the secret by hand)")
    typer.echo(out["uri"])
    typer.echo(f"secret: {out['secret']}\n")
    typer.echo("Recovery codes (each works once; store them offline, e.g. in Bitwarden, not on this host):")
    for c in out["recovery_codes"]:
        typer.echo(f"  {c}")
    typer.echo(f"\nstored in {store.path} (0600). Operator sign-in now asks for a code; restart not needed.")


@totp_app.command("disable")
def totp_disable() -> None:
    if not _totp().disable():
        typer.echo("TOTP was not enabled")
        return
    typer.echo("TOTP disabled. With SHIMPAKU_PUBLIC_VIEW=1 the operator cannot sign in until it is enabled again.")


@totp_app.command("status")
def totp_status() -> None:
    st = _totp().status()
    if not st["enabled"]:
        typer.echo("TOTP: disabled")
        return
    typer.echo(f"TOTP: enabled; {st['recovery_codes_left']} recovery codes left")


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
    typer.echo(f"purged: {out['invites']} invite notes, {out['leases']} display names, "
               f"{out.get('requests', 0)} session requests")


# ---- session requests ---------------------------------------------------------------------------
requests_app = typer.Typer(help="Live-session requests from the public Request page.", no_args_is_help=True)
admin_app.add_typer(requests_app, name="requests")


@requests_app.command("list")
def requests_list(status: str = typer.Option(None, "--status", help="pending|approved|declined|expired|used")) -> None:
    for r in _leases().requests.list(status):
        slots = ", ".join(_when(t) for t in r["slots"])
        chosen = f" slot {_when(r['slot_start'])}" if r["slot_start"] else ""
        who = "(purged)" if r["purged"] else f"{r['name']} <{r['contact']}>"
        typer.echo(f"{r['id']}  {r['status']:<8} {who}{chosen}\n    asked: {slots}"
                   + (f"\n    {r['message']}" if r["message"] else ""))


@requests_app.command("approve")
def requests_approve(
    request_id: str,
    choice: int = typer.Option(None, "--choice", help="Use the requester's slot 1, 2 or 3."),
    slot: str = typer.Option(None, "--slot", help="Another start, ISO 8601 with a timezone (2026-11-02T15:00+05:30)."),
) -> None:
    from datetime import datetime

    from shimpaku.api.app import invite_link
    from shimpaku.api.requests import RequestError

    if not settings.public_url:
        typer.echo("refused: set SHIMPAKU_PUBLIC_URL (the fixed https base the link is built from)", err=True)
        raise typer.Exit(2)
    rq = _leases().requests
    try:
        if slot:
            dt = datetime.fromisoformat(slot)
            if dt.tzinfo is None:
                typer.echo("refused: give the slot with a timezone", err=True)
                raise typer.Exit(2)
            start = dt.timestamp()
        else:
            slots = rq.get(request_id)["slots"]
            if not choice or not 1 <= choice <= len(slots):
                typer.echo(f"refused: pass --choice 1..{len(slots)} or --slot", err=True)
                raise typer.Exit(2)
            start = slots[choice - 1]
        out = rq.approve(request_id, start, "cli")
    except RequestError as e:
        typer.echo(f"refused: {e}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"approved {request_id}: {_when(out['slot_start'])} - {_when(out['slot_end'])}"
               f" (link opens {_when(out['opens_at'])}):")
    typer.echo(invite_link(out["invite_token"]))
    typer.echo(f"calendar file: {settings.public_url.rstrip('/')}/api/invites/ics?i={out['invite_token']}")


@requests_app.command("decline")
def requests_decline(request_id: str, note: str = typer.Option("", "--note", help="Shown to the requester.")) -> None:
    from shimpaku.api.requests import RequestError

    try:
        _leases().requests.decline(request_id, note, "cli")
    except RequestError as e:
        typer.echo(f"refused: {e}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"declined {request_id}")


if __name__ == "__main__":
    admin_app()
