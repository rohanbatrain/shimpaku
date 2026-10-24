"""`shimpaku mcp-stdio`: a stdio MCP server that forwards every request to shimpakud's `/mcp`.

For clients that spawn a process (Claude Code / OpenCode over `ssh sitar …`). It never opens the
database or a device: the daemon does policy, audit and execution, so there is one path only.
It labels itself with `X-Shimpaku-Client: stdio-proxy:<os user>`; a bearer token (for
auth_mode=local) is read from `--token-file` (default `~/.config/shimpaku/mcp.token` if present).

    python -m shimpaku.mcp.proxy [--url http://127.0.0.1:8780/mcp] [--token-file PATH]
"""

from __future__ import annotations

import argparse
import getpass
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from shimpaku.config import settings


def build_proxy(url: str, token: str | None = None) -> Server:
    headers = {"X-Shimpaku-Client": f"stdio-proxy:{getpass.getuser()}"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    @asynccontextmanager
    async def lifespan(_server: Server) -> Any:
        import httpx2

        async with httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(120, read=300)) as http:
            async with Client(streamable_http_client(url, http_client=http)) as up:
                yield {"up": up}

    async def list_tools(ctx, params):
        return await ctx.lifespan_context["up"].list_tools()

    async def call_tool(ctx, params):
        return await ctx.lifespan_context["up"].call_tool(params.name, params.arguments or {})

    async def list_resources(ctx, params):
        return await ctx.lifespan_context["up"].list_resources()

    async def list_resource_templates(ctx, params):
        return await ctx.lifespan_context["up"].list_resource_templates()

    async def read_resource(ctx, params):
        return await ctx.lifespan_context["up"].read_resource(params.uri)

    async def list_prompts(ctx, params):
        return await ctx.lifespan_context["up"].list_prompts()

    async def get_prompt(ctx, params):
        return await ctx.lifespan_context["up"].get_prompt(params.name, params.arguments or {})

    return Server("shimpaku", version="0.1.0", lifespan=lifespan,
                  instructions="Proxy to shimpakud /mcp (policy, approvals and audit run in the daemon).",
                  on_list_tools=list_tools, on_call_tool=call_tool, on_list_resources=list_resources,
                  on_list_resource_templates=list_resource_templates, on_read_resource=read_resource,
                  on_list_prompts=list_prompts, on_get_prompt=get_prompt)


async def serve(url: str, token: str | None) -> None:
    srv = build_proxy(url, token)
    async with stdio_server() as (r, w):
        await srv.run(r, w, srv.create_initialization_options())


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="shimpaku mcp-stdio")
    ap.add_argument("--url", default=f"http://127.0.0.1:{settings.port}/mcp")
    ap.add_argument("--token-file", type=Path, default=Path.home() / ".config/shimpaku/mcp.token")
    a = ap.parse_args(argv)
    token = a.token_file.read_text().strip() if a.token_file and a.token_file.exists() else None
    anyio.run(serve, a.url, token)


if __name__ == "__main__":
    main()
