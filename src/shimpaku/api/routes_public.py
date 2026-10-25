"""Public read routes: the probe heatmap and the landing page's status summary.

``app.py`` mounts it with ``app.include_router(routes_public.router(call, reader))``.

- ``GET /api/probes/heatmap`` (``reader``: the same auth, CSRF and lease rules as every GET): stored
  RPM results as a probe-path × time grid (``analytics/heatmap.py``). Paths are named by device and
  test; target addresses are never returned.
- ``GET /api/public/summary`` (no sign-in): the handful of aggregate numbers the static landing page
  shows. It is fetched cross-origin (the landing is ``<domain>``, the app ``<label>-app.<parent>``), so it
  answers CORS for the origins in ``SHIMPAKU_PUBLIC_CORS_ORIGINS`` only, GET only, never with
  credentials. It returns counts, statuses and timestamps: no device addresses, config, tokens,
  user names or tester identities. Rate limited per client address (category ``read``) and cached.

Honesty: every value is computed from a service payload; what cannot be known is ``None`` with a
``basis`` saying why (the replay cannot know which devices are up now).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from shimpaku.api import view
from shimpaku.config import settings

SUMMARY_TTL_S = 20.0
# "executed" = the action has at least one Junos commit recorded (its change reached a device)
UNSAFE_DEFINITION = ("actions that committed a change to a device although the guardrail policy did not "
                     "allow them (policy.allowed is not true), over every action in the action record")


def cors_origins() -> set[str]:
    return {o.strip().rstrip("/") for o in settings.public_cors_origins.split(",") if o.strip()}


def unsafe_executed(actions: list[dict]) -> int:
    """Actions with a device commit whose recorded policy decision was not ``allowed``."""
    return sum(1 for a in actions if a.get("commits") and (a.get("policy") or {}).get("allowed") is not True)


def router(call: Callable[..., Awaitable[Any]], reader: Callable[..., Any]) -> APIRouter:
    r = APIRouter()
    cache: dict[str, Any] = {"t": 0.0, "data": None}
    lock = asyncio.Lock()

    async def soft(name: str, *a: Any, **kw: Any) -> dict:
        try:
            out = await call(name, *a, **kw)
        except HTTPException as e:
            return {"available": False, "reason": str(e.detail)[:200]}
        except Exception as e:  # noqa: BLE001
            return {"available": False, "reason": type(e).__name__}
        return out if isinstance(out, dict) else {"available": False, "reason": "unexpected payload"}

    @r.get("/api/probes/heatmap")
    async def probes_heatmap(request: Request, site: str = "campus", window: int = 60, p=Depends(reader)) -> dict:
        window = max(5, min(int(window), 10080))
        if getattr(request.app.state.service, "DEMO", False):
            from shimpaku.api import demo_public
            return demo_public.probe_heatmap(site, window)
        return await call("probe_heatmap", site, window_min=window)

    async def build_summary(service: Any) -> dict:
        replay = bool(getattr(service, "REPLAY", False))
        inv, acts, aud, routing = await asyncio.gather(
            soft("inventory_list"), soft("action_list"), soft("audit_verify"),
            soft("routing_get_health") if not replay else asyncio.sleep(0, {"available": False}))
        nodes = inv.get("nodes") or inv.get("devices") or []
        total = len(nodes) if inv.get("available", True) else None
        if replay:
            up, basis = None, "the lab is offline; a recorded replay cannot say which devices are up now"
        elif routing.get("available") is False or not isinstance(routing.get("devices"), list):
            up, basis = None, "live device reads are unavailable"
        else:
            up = sum(1 for d in routing["devices"] if isinstance(d, dict) and not d.get("error"))
            basis = "devices answering a live routing read (NETCONF)"
        raw = acts.get("actions") if acts.get("available", True) else None
        rows = view.action_list(acts).get("actions") or [] if raw is not None else []
        details = raw or []
        if raw and not any("commits" in a for a in raw):
            # the replay's list is view-shaped; its per-action records hold policy and commits
            details = await asyncio.gather(*(soft("action_get", a.get("id")) for a in raw if a.get("id")))
        last = max(rows, key=lambda a: a.get("updated_at") or a.get("created_at") or 0, default=None)
        audit = None
        if aud.get("available", True) and "ok" in aud:
            audit = {"verified": bool(aud.get("ok")), "rows": aud.get("rows"),
                     "verified_at": aud.get("verified_at") or time.time()}
        info = service.info() if replay and callable(getattr(service, "info", None)) else None
        return {
            "generated_at": time.time(),
            "lab": {"live": not replay, "replay_taken_at": (info or {}).get("taken_at")},
            "devices": {"up": up, "total": total, "basis": basis},
            "last_action": ({"at": last.get("updated_at") or last.get("created_at"), "status": last.get("status"),
                             "kind": last.get("kind")} if last else None),
            "actions": {"total": len(rows) if raw is not None else None,
                        "blocked": sum(1 for a in rows if a.get("status") == "blocked") if raw is not None else None,
                        "unsafe_executed": unsafe_executed(details) if raw is not None else None,
                        "unsafe_definition": UNSAFE_DEFINITION},
            "audit": audit,
            **({"demo": True} if getattr(service, "DEMO", False) else {}),
        }

    @r.get("/api/public/summary")
    async def public_summary(request: Request) -> JSONResponse:
        limiter = getattr(request.app.state, "limiter", None)
        if limiter is not None:
            ip = request.client.host if request.client else "?"
            wait = limiter.check("read", f"ip:{ip}")
            if wait is not None:
                raise HTTPException(429, f"rate limit (read); retry in {wait} s", headers={"Retry-After": str(wait)})
        async with lock:
            if cache["data"] is None or time.time() - cache["t"] > SUMMARY_TTL_S:
                cache["data"] = await build_summary(request.app.state.service)
                cache["t"] = time.time()
            data = cache["data"]
        headers = {"Cache-Control": "public, max-age=15", "Vary": "Origin"}
        origin = (request.headers.get("origin") or "").rstrip("/")
        if origin and origin in cors_origins():
            headers["Access-Control-Allow-Origin"] = origin
            headers["Access-Control-Allow-Methods"] = "GET"
        return JSONResponse(data, headers=headers)

    return r
