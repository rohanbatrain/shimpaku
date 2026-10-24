"""`shimpaku` — the operator-facing CLI. `shimpakud` (the daemon) is a separate entry point.

Subcommands come from each builder's own module; this file only assembles them (CONTRACT:
"the orchestrator wires `shimpaku.cli:app`").
"""

from __future__ import annotations

import typer

from shimpaku.api.cli_admin import admin_app
from shimpaku.assistant.cli import ask
from shimpaku.demo_cli import app as demo_app

app = typer.Typer(add_completion=False, help="Shimpaku: a Mist-style assurance NMS for one Junos home lab.")
app.add_typer(admin_app, name="admin")
app.add_typer(demo_app, name="demo")
app.command("ask")(ask)  # `shimpaku ask "question"`


@app.command("mcp-stdio", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def mcp_stdio(ctx: typer.Context) -> None:
    """Stdio MCP proxy to the running daemon's /mcp — what Claude Code / OpenCode spawn."""
    from shimpaku.mcp.proxy import main as proxy_main

    proxy_main(ctx.args)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
