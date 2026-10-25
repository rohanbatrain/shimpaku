"""Virga live, the operator's fault lab (``/api/virga/*``, docs/build/virga-live.md §2).

Reads (catalogue, live state, its SSE stream, history) use ``reader``: everyone who may read the
dashboard may watch, visitors included (D3). The payloads are built from an allow-list in
``shimpaku.virga_live``: no config lines, no management addresses, the actor reduced to a role.

Writes (inject, revert now, recover) need ``Principal.can_present``: the operator's own dashboard
session (password + TOTP on the public site). Every bearer token, tester, visitor and anonymous
caller gets 403, and so does the replay (D7). Cookie writes pass ``reader``'s Origin + CSRF check.
Injects are rate limited per principal in category ``virga`` (default 6 per HOUR, 12 globally;
``SHIMPAKU_RATE_LIMITS virga=a/b`` is read per hour for this category, D10); revert and recover
spend the ordinary ``write`` budget. Every attempt, allowed or refused, is one hash-chained audit row
(``virga_inject``, ``virga_revert``, ``virga_recover``, D8): refusals are written here, accepted
operations by the manager when their outcome is known (an inject's row when its commit is done).

``app.py`` mounts it with one line: ``app.include_router(routes_virga.router(call, reader, rate))``.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from shimpaku import virga_live as vl
from shimpaku.api.auth import Principal
from shimpaku.api.ratelimit import RateLimiter

VIRGA_RATE = (6, 12)   # injects per principal / globally, per hour
STREAM_TICK_S = 1.0
NOT_OPERATOR = "fault injection is for the operator's own signed-in session"


class TrialBody(BaseModel):
    scenario: str = Field(default="", max_length=64)  # an unknown id is the manager's 422 (and audited)
    hold_s: int | None = None


def hourly_limiter(app: Any) -> RateLimiter:
    """Category ``virga`` in hours: the shared limiter counts per minute, so this one runs the same
    token buckets on a clock in minutes (``per_min`` then means per hour). Tests may set their own."""
    lim = getattr(app.state, "virga_limiter", None)
    if lim is None:
        table = getattr(getattr(app.state, "limiter", None), "table", {}) or {}
        lim = RateLimiter({"virga": table.get("virga", VIRGA_RATE)}, clock=lambda: time.monotonic() / 60)
        app.state.virga_limiter = lim
    return lim


def router(call: Callable[..., Any], reader: Callable[..., Principal], rate: Callable[[str, Any], None]) -> APIRouter:
    r = APIRouter()

    def replay(request: Request) -> bool:
        return bool(getattr(request.app.state.service, "REPLAY", False))

    def gate(request: Request, p: Principal, method: str, args: dict) -> dict:
        """Who may write: refusals are audited and raised; the operator's caller dict is returned."""
        c = vl.caller_of(p)
        if replay(request):
            refuse(method, c, args, 403, "replay", vl.REPLAY_REASON)
        if not p.can_present:
            refuse(method, c, args, 403, "not_operator", NOT_OPERATOR)
        return c

    def refuse(method: str, c: dict, args: dict, status: int, code: str, message: str, **kw: Any) -> None:
        vl.audit(method, c, {**args, "outcome": f"refused:{code}"}, f"refused:{code}")
        raise HTTPException(status, message, **kw)

    # ---- reads -------------------------------------------------------------------------------
    @r.get("/api/virga/catalogue")
    async def catalogue(request: Request, p: Principal = Depends(reader)) -> dict:
        return await asyncio.to_thread(vl.manager_for(request.app).catalogue_view)

    @r.get("/api/virga/state")
    async def state(request: Request, p: Principal = Depends(reader)) -> dict:
        return vl.manager_for(request.app).state()

    @r.get("/api/virga/trials")
    async def trials(request: Request, p: Principal = Depends(reader)) -> dict:
        return await asyncio.to_thread(vl.manager_for(request.app).trials_view)

    @r.get("/api/virga/stream")
    async def stream(request: Request, p: Principal = Depends(reader)):
        m = vl.manager_for(request.app)

        async def gen():
            last = None
            while not await request.is_disconnected():
                blob = json.dumps(m.state(), default=str)
                if blob != last:
                    last = blob
                    yield {"event": "state", "data": blob}
                await asyncio.sleep(STREAM_TICK_S)

        return EventSourceResponse(gen(), ping=15)

    # ---- writes ------------------------------------------------------------------------------
    @r.post("/api/virga/trials", status_code=202)
    async def inject(body: TrialBody, request: Request, p: Principal = Depends(reader)) -> dict:
        args = {"scenario": body.scenario[:32], "hold_s": body.hold_s}
        c = gate(request, p, "virga_inject", args)
        key = p.token_id or f"{p.name}:{p.lease_id or ''}"
        wait = hourly_limiter(request.app).check("virga", key)
        if wait is not None:
            refuse("virga_inject", c, args, 429, "rate_limited", f"rate limit (virga); retry in {wait * 60} s",
                   headers={"Retry-After": str(wait * 60)})
        m = vl.manager_for(request.app)
        if hasattr(m, "incident_check"):
            m.incident_check = vl.incident_checker(lambda: call("incident_list"), asyncio.get_running_loop())
        try:
            tid = await asyncio.to_thread(m.start, body.scenario, body.hold_s, c)
        except vl.Refused as e:
            refuse("virga_inject", c, args, e.status, e.code, e.message)
        return {"trial_id": tid}

    @r.post("/api/virga/trials/{trial_id}/revert", status_code=202)
    async def revert(trial_id: str, request: Request, p: Principal = Depends(reader)) -> dict:
        args = {"trial_id": trial_id[:80]}
        c = gate(request, p, "virga_revert", args)
        rate("write", p)
        try:
            vl.manager_for(request.app).revert(trial_id, c)
        except vl.Refused as e:
            refuse("virga_revert", c, args, e.status, e.code, e.message)
        return {"ok": True}

    @r.post("/api/virga/recover")
    async def recover(request: Request, p: Principal = Depends(reader)) -> JSONResponse:
        c = gate(request, p, "virga_recover", {})
        rate("write", p)
        try:
            out = await asyncio.to_thread(vl.manager_for(request.app).recover, c)
        except vl.Refused as e:
            refuse("virga_recover", c, {}, e.status, e.code, e.message)
        return JSONResponse(out)

    return r
