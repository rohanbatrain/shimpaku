"""``shimpaku admin set-password``: store the admin's argon2id hash in ``<data>/admin.hash`` (0600).

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


if __name__ == "__main__":
    admin_app()
