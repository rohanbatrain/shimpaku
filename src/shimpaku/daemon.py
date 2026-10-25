"""`shimpakud` — the one process that owns state (CONTRACT "Process model").

Runs, in one asyncio process: the gNMI collector, the analytics loop, the actions engine
(executor + validation timers), the MCP server (Streamable HTTP at `/mcp`), and the FastAPI
app (REST + SSE + the built dashboard at `/`) — all on one port, dispatched by path.

Everything each builder wrote (collector, analytics, mcp/server, api/app, service, providers)
is used as-is; this file only wires them together and owns the top-level lifecycle.
"""

from __future__ import annotations

import os

import asyncio
import logging
import signal

import uvicorn

from shimpaku import devices, providers, service, topology
from shimpaku.analytics.loop import run_analytics_loop
from shimpaku.api.app import create_app
from shimpaku.collector import Collector
from shimpaku.config import settings
from shimpaku.mcp.server import McpHttpGate, build_server, http_app, set_token_verifier

log = logging.getLogger("shimpaku.daemon")


class CombinedASGI:
    """Dispatch by path prefix. `/mcp` is handled by a real second ASGI app, not a mount, because
    the FastAPI app's SPA catch-all (`GET /{path:path}`) would otherwise swallow `GET /mcp`
    (the MCP Streamable HTTP SSE stream) before Starlette ever reaches a mount at the same path.
    The lifespan scope is acked here directly — this class starts and stops the daemon's own
    resources itself (below), outside the ASGI lifespan protocol, so it never forwards lifespan
    to either sub-app."""

    def __init__(self, mcp_app, rest_app) -> None:
        self.mcp_app = mcp_app
        self.rest_app = rest_app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "lifespan":
            while True:
                msg = await receive()
                if msg["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif msg["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
            return
        path = scope.get("path", "")
        app = self.mcp_app if path == "/mcp" or path.startswith("/mcp/") else self.rest_app
        await app(scope, receive, send)


async def async_main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings.require_device_password()
    settings.ensure_data()
    log.info("data dir: %s", settings.data)

    from shimpaku.store import Store

    store = Store(settings.duckdb_path).connect().init()
    topo = topology.sync(store, topology.load())
    log.info("topology: %d nodes, %d links", len(topo.nodes()), len(topo.links()))

    providers.register_all(store)
    devices.Pool()  # module-level pool is created lazily; touch it so failures surface early

    mcp_server = build_server()  # wires service.set_audit_log + executor audit internally
    service.set_mcp_server(mcp_server)  # Ask Shim (dashboard) talks to it in memory
    mcp_app = http_app(mcp_server)
    rest_app = create_app(service, store, settings.sqlite_path)
    leases = rest_app.state.leases
    # bearer tokens on /mcp verify against the AuthStore (expiry, revocation) and, for a
    # tester's token, its lease being active; 401/403/429 are answered before the MCP app
    set_token_verifier(leases.verify_token)
    dispatcher = CombinedASGI(McpHttpGate(mcp_app, leases.verify_token, rest_app.state.limiter), rest_app)

    from shimpaku.actions.engine import engine

    tasks: list[asyncio.Task] = []
    async with mcp_app.router.lifespan_context(mcp_app):
        tasks.append(asyncio.create_task(Collector(store, topo).run(), name="collector"))
        tasks.append(asyncio.create_task(run_analytics_loop(store), name="analytics"))
        tasks.append(engine().start())
        # tester leases: timeouts, end-of-lease cleanup, queue promotion, daily retention purge
        tasks.append(asyncio.create_task(leases.run_forever(), name="leases"))
        # lab host health: sysfs/proc + virsh, no root; rolling 24 h in <data>/host.db (/api/ops/host)
        from shimpaku.hostmon import HostSampler, HostStore
        rest_app.state.hostmon = HostStore(settings.data / "host.db")
        tasks.append(asyncio.create_task(HostSampler(rest_app.state.hostmon, interval_s=settings.host_sample_s,
                                                     guard=settings.thermal_guard).run(), name="hostmon"))
        if settings.virga_live:
            # Virga live (docs/build/virga-live.md): load the catalogue and read the journal now, so a
            # bad path or a crashed trial (needs_recovery) shows in the log at start, not at the first inject
            from shimpaku import virga_live
            try:
                rest_app.state.virga_live = virga_live.VirgaLive.from_settings()
                rest_app.state.virga_live.catalogue()
                log.info("virga live: on, %d scenarios", len(rest_app.state.virga_live.catalogue().scenarios))
            except Exception as e:  # noqa: BLE001  (the rest of the daemon runs; injects are refused or fail)
                log.error("virga live: not ready: %s: %s", type(e).__name__, e)

        config = uvicorn.Config(dispatcher, host=settings.bind, port=settings.port, log_level="info",
                            # behind the edge: trust its X-Forwarded-* so cookies are Secure/__Host- and
                            # rate limits see real client IPs (same as api/__main__.py)
                            proxy_headers=True,
                            forwarded_allow_ips=os.environ.get("SHIMPAKU_TRUSTED_PROXIES", "127.0.0.1"))
        server = uvicorn.Server(config)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)

        log.info("shimpakud on %s:%s (/api, /mcp, dashboard at /)", settings.bind, settings.port)
        serve_task = asyncio.create_task(server.serve())
        await stop.wait()
        server.should_exit = True
        await serve_task

        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        store.close()
        log.info("shimpakud stopped cleanly")


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
