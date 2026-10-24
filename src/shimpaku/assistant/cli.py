"""`shimpaku ask "<question>"` — Shim from the terminal (streams tokens, shows the tool trace on stderr)."""

from __future__ import annotations

import asyncio
import json
import sys

import typer

from shimpaku.assistant.loop import Assistant, default_mcp_url

app = typer.Typer(add_completion=False, help="Shim, the Shimpaku assistant.")


async def _run(question: str, model: str, mcp_url: str, max_steps: int, as_json: bool) -> int:
    rc = 0
    async for ev in Assistant(mcp_url, model=model, max_steps=max_steps).ask(question):
        t = ev["type"]
        if as_json:
            print(json.dumps(ev), flush=True)
        elif t == "token":
            sys.stdout.write(ev["text"])
            sys.stdout.flush()
        elif t == "route":
            print(f"[shim] {ev['role'] or 'model'} -> {ev['model']} ({ev['reason']})", file=sys.stderr)
        elif t == "tool_call":
            print(f"[shim] step {ev['step']}: {ev['name']}({json.dumps(ev['args'])})", file=sys.stderr)
        elif t == "tool_result":
            ids = " ".join(f"{i['kind']}:{i['id']}" for i in ev["ids"])
            print(f"[shim]   -> {ev['status']} {ev['ms']} ms {ids}", file=sys.stderr)
        elif t == "error":
            print(f"\n[shim] error: {ev['message']}", file=sys.stderr)
            rc = 1
        elif t == "done":
            print(f"\n[shim] {ev['steps']} steps, {ev['wall_s']} s, models {', '.join(ev['models'])}", file=sys.stderr)
    return rc


@app.command()
def ask(
    question: str = typer.Argument(..., help='e.g. "what\'s the fabric looking like?"'),
    model: str = typer.Option("auto", "--model", "-m", help="auto | triage | diagnose | escalate | gateway model name"),
    mcp_url: str = typer.Option(None, "--mcp", help="shimpakud MCP endpoint (default from settings)"),
    max_steps: int = typer.Option(8, "--max-steps"),
    as_json: bool = typer.Option(False, "--json", help="print the raw event stream as JSON lines"),
) -> None:
    """Ask Shim a question about the lab."""
    raise typer.Exit(asyncio.run(_run(question, model, mcp_url or default_mcp_url(), max_steps, as_json)))


if __name__ == "__main__":
    app()
