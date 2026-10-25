"""Operator-only ops panels: lab host health (``/api/ops/host``) and the LLM gateway
(``/api/ops/gateway``).

``app.py`` mounts this with ``app.include_router(routes_ops.router(operator, app, demo))``: every
route depends on the app's own ``operator`` dependency, so visitors, testers, bearer tokens and the
replay host get 403/401 exactly as on the other admin routes. Neither route returns a secret: the
host panel carries sensor readings and VM names; the gateway panel masks keys to alias / last 4.

The daemon puts a running ``HostSampler``'s store at ``app.state.hostmon``; without one (the API
started alone) the host route takes a single live reading and says the sampler is not running.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from fastapi import APIRouter, Depends, FastAPI

from shimpaku import hostmon
from shimpaku.api import demo_ops
from shimpaku.config import settings
from shimpaku.gateway import GatewayClient


def router(operator: Callable[..., Any], app: FastAPI, demo: bool) -> APIRouter:
    r = APIRouter(prefix="/api/ops", dependencies=[Depends(operator)])
    gw: dict[str, GatewayClient] = {}

    @r.get("/host")
    async def host() -> dict:
        if demo:
            return demo_ops.host()
        store = getattr(app.state, "hostmon", None)
        if store is not None:
            return hostmon.snapshot(store, guard_enabled=settings.thermal_guard)
        tmp = hostmon.HostStore()
        s = await asyncio.to_thread(hostmon.read_sample)
        tmp.add(s)
        tmp.vms = {**await hostmon.virsh_domains(), "t": s["t"]}
        return hostmon.snapshot(tmp, guard_enabled=settings.thermal_guard, running=False)

    @r.get("/gateway")
    async def gateway() -> dict:
        if demo:
            return demo_ops.gateway()
        client = getattr(app.state, "gateway", None) or gw.get("c")
        if client is None:
            client = gw["c"] = GatewayClient(settings.llm_base, settings.llm_admin_key)
        return {**await client.get(), "base": _host_only(client.base)}

    return r


def _host_only(url: str) -> str:
    """Scheme and host for display; no path, query or userinfo (which could carry a credential)."""
    from urllib.parse import urlsplit
    u = urlsplit(url)
    return f"{u.scheme}://{u.hostname or ''}{':' + str(u.port) if u.port else ''}"
