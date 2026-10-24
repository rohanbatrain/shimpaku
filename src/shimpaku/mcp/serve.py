"""Standalone runner: MCP over Streamable HTTP at /mcp plus the executor/validation loop.

The daemon (`shimpakud`, orchestrator) mounts `http_app()` and starts `engine().start()` itself;
this module exists so the MCP + actions slice can run on its own:

    python -m shimpaku.mcp.serve            # binds settings.bind:settings.port
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from shimpaku.actions.engine import engine
from shimpaku.config import settings
from shimpaku.mcp.server import build_server, http_app


def create_app():
    app = http_app(build_server())
    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(a):
        async with inner(a):
            task = engine().start()
            try:
                yield
            finally:
                task.cancel()

    app.router.lifespan_context = lifespan
    return app


def main() -> None:
    import logging

    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(), host=settings.bind, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
