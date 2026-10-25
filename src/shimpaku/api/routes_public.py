"""Public read routes: the probe heatmap and the landing page's status summary.

``app.py`` mounts it with ``app.include_router(routes_public.router(call, reader))``.

- ``GET /api/probes/heatmap`` (``reader``: the same auth, CSRF and lease rules as every GET): stored
  RPM results as a probe-path × time grid (``analytics/heatmap.py``). Paths are named by device and
  test; target addresses are never returned.
- ``GET /api/public/summary`` (no sign-in): the handful of aggregate numbers the static landing page
  shows, and the ``drill`` block (is a Virga fault drill running, docs/build/virga-live.md §2). It is fetched cross-origin (the landing is ``<domain>``, the app ``<label>-app.<parent>``), so it
  answers CORS for the origins in ``SHIMPAKU_PUBLIC_CORS_ORIGINS`` only, GET only, never with
  credentials. It returns counts, statuses and timestamps: no device addresses, config, tokens,
  user names or tester identities. Rate limited per client address (category ``read``) and cached.
- ``GET /api/public/health`` (no sign-in): bare words for the status page's keyword monitors
  (Uptime Kuma): per site ``healthy`` / ``degraded`` / ``offline``, ``drill`` idle / active, ``lab``
  live / replay. ``degraded`` = the site's latest SLE minute is below its goal (100 % when none is
  set) or a device of the site has no stored health sample (its reads are failing); ``offline`` =
  every device of the site is failing, or nothing about the site can be read. Stored reads only, no
  device call; cached 10 s; same rate limit and CORS as the summary.

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
HEALTH_TTL_S = 10.0
NO_DRILL = {"active": False, "scenario": None, "title": None, "phase": None, "since": None}
# "executed" = the action has at least one Junos commit recorded (its change reached a device)
UNSAFE_DEFINITION = ("actions that committed a change to a device although the guardrail policy did not "
                     "allow them (policy.allowed is not true), over every action in the action record")


def cors_origins() -> set[str]:
    return {o.strip().rstrip("/") for o in settings.public_cors_origins.split(",") if o.strip()}


def unsafe_executed(actions: list[dict]) -> int:
    """Actions with a device commit whose recorded policy decision was not ``allowed``."""
    return sum(1 for a in actions if a.get("commits") and (a.get("policy") or {}).get("allowed") is not True)


def drill_of(app: Any) -> dict:
    """The Virga drill block; never fails the page it is part of."""
    from shimpaku import virga_live
    try:
        return virga_live.manager_for(app).drill()
    except Exception:  # noqa: BLE001
        return dict(NO_DRILL)


def latest_below_goal(sle: Any) -> bool | None:
    """True when any SLE's newest minute is below its goal; None when the site has no SLE data."""
    v = view.sle_summary(sle) if isinstance(sle, dict) else None
    if not isinstance(v, dict) or v.get("available") is False or "error" in v or not v.get("sles"):
        return None
    for s in v["sles"]:
        goal = s.get("goal_pct") if isinstance(s.get("goal_pct"), (int, float)) else 100
        pts = [p for p in s.get("series") or [] if isinstance(p.get("value"), (int, float))]
        last = max(pts, key=lambda p: view.ts(p.get("t")) or 0)["value"] if pts else s.get("value_pct")
        if isinstance(last, (int, float)) and last < goal:
            return True
    return False


def site_health(sle: Any, inv: dict, health: dict, site: str) -> str:
    nodes = [d.get("name") for d in (inv.get("nodes") or inv.get("devices") or [])
             if isinstance(d, dict) and d.get("site") == site]
    rows = {d.get("device"): d for d in health.get("devices") or [] if isinstance(d, dict)} \
        if isinstance(health.get("devices"), list) else None
    failing = [n for n in nodes if rows is not None and not (rows.get(n) or {}).get("sampled_at")]
    below = latest_below_goal(sle)
    if (nodes and len(failing) == len(nodes)) or (below is None and rows is None):
        return "offline"
    return "degraded" if failing or below else "healthy"


def router(call: Callable[..., Awaitable[Any]], reader: Callable[..., Any]) -> APIRouter:
    r = APIRouter()
    cache: dict[str, Any] = {"t": 0.0, "data": None}
    hcache: dict[str, Any] = {"t": 0.0, "data": None}
    lock = asyncio.Lock()
    hlock = asyncio.Lock()

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

    def limit(request: Request) -> None:
        limiter = getattr(request.app.state, "limiter", None)
        if limiter is not None:
            ip = request.client.host if request.client else "?"
            wait = limiter.check("read", f"ip:{ip}")
            if wait is not None:
                raise HTTPException(429, f"rate limit (read); retry in {wait} s", headers={"Retry-After": str(wait)})

    def public_headers(request: Request, max_age: int) -> dict:
        headers = {"Cache-Control": f"public, max-age={max_age}", "Vary": "Origin"}
        origin = (request.headers.get("origin") or "").rstrip("/")
        if origin and origin in cors_origins():
            headers["Access-Control-Allow-Origin"] = origin
            headers["Access-Control-Allow-Methods"] = "GET"
        return headers

    @r.get("/api/public/summary")
    async def public_summary(request: Request) -> JSONResponse:
        limit(request)
        async with lock:
            if cache["data"] is None or time.time() - cache["t"] > SUMMARY_TTL_S:
                cache["data"] = await build_summary(request.app.state.service)
                cache["t"] = time.time()
            data = cache["data"]
        # the drill is in memory and changes by the second: never from the 20 s cache
        return JSONResponse({**data, "drill": drill_of(request.app)}, headers=public_headers(request, 15))

    async def build_health(app: Any) -> dict:
        if getattr(app.state.service, "REPLAY", False):
            return {"campus": "offline", "fabric": "offline", "drill": "idle", "lab": "replay"}
        inv, health, campus, fabric = await asyncio.gather(
            soft("inventory_list"), soft("device_get_health", window_min=5),
            soft("sle_get_summary", "campus", window_min=5), soft("sle_get_summary", "fabric", window_min=5))
        return {"campus": site_health(campus, inv, health, "campus"), "fabric": site_health(fabric, inv, health, "fabric"),
                "drill": "active" if drill_of(app)["active"] else "idle", "lab": "live"}

    @r.get("/api/public/health")
    async def public_health(request: Request) -> JSONResponse:
        limit(request)
        async with hlock:
            if hcache["data"] is None or time.time() - hcache["t"] > HEALTH_TTL_S:
                hcache["data"] = await build_health(request.app)
                hcache["t"] = time.time()
            data = hcache["data"]
        return JSONResponse(data, headers=public_headers(request, int(HEALTH_TTL_S)))

    return r
