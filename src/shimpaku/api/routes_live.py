"""Live views (``/api/live/*``): link traffic on the topology, the Junos event stream, and
as-of-minute state for the time-travel scrubber. Read-only.

``app.py`` mounts it with ``app.include_router(routes_live.router(call, reader, service))``.
Data comes from ``shimpaku.providers_live`` (store reads registered as service providers) or,
in demo mode, from ``api/demo_live.py``; a service with neither answers ``available: false``.

Honesty: rates are Δcounter / Δt between two stored samples, never interpolated; link speed is
not collected, so utilisation is ``None`` with the reason. As-of link state uses only what is
stored per minute (port oper status, OSPF adjacency per interface); BFD is stored per peer
address, not per interface, so it is not used and the payload says so.

Guest safety (visitors, testers): no management addresses (every message is host-scrubbed),
no configuration text (``UI_CFG_AUDIT_*`` messages and any line that looks like a secret are
replaced), and only a few event attributes survive. Operators see the stored event unchanged.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from shimpaku.api import routes_viz, view

TRAFFIC_TICK_S = 5.0
EVENTS_TICK_S = 2.0
TOPO_TTL_S = 60.0
RANGE_TTL_S = 30.0
MAX_HISTORY_S = 24 * 3600
STALE_S = 60.0
GUEST_ATTRS = ("priority", "facility", "daemon", "interface-name")
_SECRET = re.compile(r"(encrypted-password|secret|authentication-key|pre-shared-key|community|ssh-(rsa|ed25519|ecdsa)|"
                     r"plain-text-password|\$[1569]\$|\$9\$)", re.I)
CONFIG_HIDDEN = "configuration change (text hidden from guests)"
BFD_NOTE = "BFD is stored per peer address, not per interface, so as-of link state uses port oper status and OSPF only"


def is_guest(p: Any) -> bool:
    return getattr(p, "effective_role", "visitor") in ("tester", "visitor")


def redact_event(e: dict, guest: bool) -> dict:
    if not guest:
        return e
    from shimpaku.service import scrub_hosts

    msg = str(e.get("message") or "")
    if str(e.get("type") or "").startswith("UI_CFG_AUDIT") or _SECRET.search(msg):
        msg = CONFIG_HIDDEN
    attrs = {k: scrub_hosts(str(v)) for k, v in (e.get("attrs") or {}).items() if k in GUEST_ATTRS}
    return {**e, "message": scrub_hosts(msg)[:500], "attrs": attrs}


def link_rates(links: list[dict], rates: dict, live: bool) -> list[dict]:
    """Per intended link: a→b and b→a bits/s from either end's counters (a's out, else b's in)."""
    ports = rates.get("ports") or {}
    now = time.time()
    out = []
    for lk in links:
        pa = (ports.get(lk["a"]) or {}).get(lk.get("a_if") or "") or {}
        pb = (ports.get(lk["b"]) or {}).get(lk.get("b_if") or "") or {}
        ab = pa.get("out_bps") if pa.get("out_bps") is not None else pb.get("in_bps")
        ba = pa.get("in_bps") if pa.get("in_bps") is not None else pb.get("out_bps")
        t = max([x for x in (pa.get("t"), pb.get("t")) if x] or [0]) or None
        reason = None
        if ab is None and ba is None:
            reason = "no counter samples for either end in the last 90 s" if not (pa or pb) else \
                "only one sample, or a counter reset: no rate yet"
        out.append({"id": lk["id"], "a": lk["a"], "a_if": lk.get("a_if"), "b": lk["b"], "b_if": lk.get("b_if"),
                    "ab_bps": ab, "ba_bps": ba, "sample_t": t,
                    "stale": bool(live and t and now - t > STALE_S), "speed_bps": rates.get("speed_bps"),
                    "util_ab": None, "util_ba": None, "reason": reason})
    return out


def link_state_at(lk: dict, ports: dict, ospf: dict) -> dict:
    good, bad = [], []
    expected = routes_viz.EXPECTED.get(lk.get("role") or "", ())
    ev: dict[str, Any] = {}
    down = False
    for side in ("a", "b"):
        dev, ifn = lk[side], lk.get(f"{side}_if")
        oper = (ports.get(dev) or {}).get(ifn or "")
        ev[f"oper_{side}"] = oper
        if oper == "down":
            down = True
            bad.append(f"{dev} {ifn} oper down")
        elif oper == "up":
            good.append(f"{dev} {ifn} oper up")
        st = (ospf.get(dev) or {}).get(ifn or "")
        ev[f"ospf_{side}"] = st
        if st == "Full":
            good.append(f"{dev} OSPF Full")
        elif st is not None and "ospf" in expected:
            bad.append(f"{dev} OSPF {st}")
    state = "down" if down else "degraded" if bad and good else "down" if bad else "up" if good else "unknown"
    return {"id": lk["id"], "a": lk["a"], "a_if": lk.get("a_if"), "b": lk["b"], "b_if": lk.get("b_if"),
            "role": lk.get("role"), "state": state, "good": good, "bad": bad, "evidence": ev}


def node_state_at(name: str, st: dict) -> dict:
    conn = (st.get("connected") or {}).get(name)
    v = (st.get("verdicts") or {}).get(name)
    state = ("down" if conn is False else "degraded" if (v or {}).get("status") == "bad" else
             "up" if conn or (v or {}).get("status") == "good" else "unknown")
    return {"name": name, "state": state, "collector_connected": conn, "verdict": v}


def router(call: Callable[..., Awaitable[Any]], reader: Callable[..., Any], service: Any = None) -> APIRouter:
    r = APIRouter()
    cache: dict[str, Any] = {}
    lock = asyncio.Lock()

    async def src(name: str, **kw: Any) -> dict:
        """A live-data read that degrades to ``available: false``."""
        try:
            if service is not None and hasattr(service, name):
                out = await call(name, **kw)
            elif getattr(service, "DEMO", False):
                from shimpaku.api import demo_live
                out = await getattr(demo_live, name)(**kw)
            else:
                from shimpaku import service as svc
                out = await svc._call_provider(name, **kw)
        except HTTPException as e:
            return {"available": False, "reason": routes_viz._scrub(e.detail)}
        except Exception as e:  # noqa: BLE001
            return {"available": False, "reason": type(e).__name__}
        if not isinstance(out, dict):
            return {"available": False, "reason": "unexpected payload"}
        if isinstance(out.get("error"), dict):
            return {"available": False, "reason": routes_viz._scrub(out["error"].get("message") or "provider failed")}
        return out

    async def soft(name: str, *a: Any, **kw: Any) -> dict:
        try:
            out = await call(name, *a, **kw)
        except HTTPException as e:
            return {"available": False, "reason": routes_viz._scrub(e.detail)}
        except Exception as e:  # noqa: BLE001
            return {"available": False, "reason": type(e).__name__}
        return out if isinstance(out, dict) else {"available": False, "reason": "unexpected payload"}

    async def cached(key: str, ttl: float, fn: Callable[[], Awaitable[Any]]) -> Any:
        async with lock:
            hit = cache.get(key)
            if hit is None or time.time() - hit[0] > ttl:
                cache[key] = hit = (time.time(), await fn())
            return hit[1]

    async def links() -> list[dict]:
        topo = await cached("topo", TOPO_TTL_S, lambda: soft("topo_get_neighbors"))
        return routes_viz.intended_links(topo)

    async def nodes() -> list[dict]:
        inv = await cached("inv", TOPO_TTL_S, lambda: soft("inventory_list"))
        return routes_viz.nodes_of(inv)

    async def range_() -> dict:
        return await cached("range", RANGE_TTL_S, lambda: src("live_range"))

    def clamp_t(t: float | None) -> float | None:
        if t is None:
            return None
        now = time.time()
        return max(now - MAX_HISTORY_S, min(float(t), now))

    async def traffic(t: float | None) -> dict:
        t = clamp_t(t)
        rates, lks = await asyncio.gather(src("live_port_rates", at=t), links())
        base = {"generated_at": time.time(), "t": t, "live": t is None, **({"demo": True} if rates.get("demo") else {})}
        if rates.get("available") is False:
            return {**base, "available": False, "reason": rates.get("reason"), "links": []}
        return {**base, "at": rates.get("at"), "window_s": rates.get("window_s"), "speed_bps": rates.get("speed_bps"),
                "speed_reason": rates.get("speed_reason"), "source": rates.get("source"),
                "links": link_rates(lks, rates, live=t is None)}

    @r.get("/api/live/traffic")
    async def live_traffic(t: float | None = None, p=Depends(reader)) -> dict:
        """Per intended link: bits/s each way from the stored gNMI counters at ``t`` (epoch s; now if absent)."""
        return await traffic(t)

    @r.get("/api/live/stream/traffic")
    async def stream_traffic(request: Request, p=Depends(reader)):
        async def gen():
            last = None
            while not await request.is_disconnected():
                blob = json.dumps(await traffic(None), default=str)
                if blob != last:
                    last = blob
                    yield {"event": "traffic", "data": blob}
                await asyncio.sleep(TRAFFIC_TICK_S)
        return EventSourceResponse(gen(), ping=15)

    def kind_ok(e: dict, kind: str | None, severity: str | None) -> bool:
        return (not kind or e.get("kind") in kind.split(",")) and (not severity or e.get("severity") in severity.split(","))

    async def events(p: Any, after_id: int | None = None, before_id: int | None = None, limit: int = 100,
                     device: str | None = None, kind: str | None = None, severity: str | None = None,
                     q: str | None = None) -> dict:
        limit = max(1, min(int(limit), 500))
        q = (q or "").strip()[:80] or None
        fetch = limit if not (kind or severity) else min(1000, limit * 10)
        raw = await src("live_events", after_id=after_id, before_id=before_id, limit=fetch, device=device or None, q=q)
        if raw.get("available") is False:
            return {"available": False, "reason": raw.get("reason"), "events": []}
        guest = is_guest(p)
        rows = [redact_event(e, guest) for e in raw.get("events") or [] if kind_ok(e, kind, severity)][:limit]
        return {"generated_at": time.time(), "events": rows, "last_id": raw.get("last_id"), "stored": raw.get("stored"),
                "first_t": raw.get("first_t"), "source": raw.get("source"),
                **({"demo": True} if raw.get("demo") else {})}

    @r.get("/api/live/events")
    async def live_events(after_id: int | None = None, before_id: int | None = None, limit: int = 100,
                          device: str | None = None, kind: str | None = None, severity: str | None = None,
                          q: str | None = None, p=Depends(reader)) -> dict:
        """Stored Junos events, newest first; filters by device, kind, severity and text."""
        return await events(p, after_id, before_id, limit, device, kind, severity, q)

    @r.get("/api/live/stream/events")
    async def stream_events(request: Request, after_id: int | None = None, p=Depends(reader)):
        """New events as the collector stores them (polled by id every 2 s; batches of ≤ 100)."""
        async def gen():
            last = after_id
            if last is None:
                head = await events(p, limit=1)
                last = head.get("last_id") or 0
                yield {"event": "hello", "data": json.dumps({"last_id": last, "available": head.get("available", True),
                                                             "reason": head.get("reason")})}
            while not await request.is_disconnected():
                out = await events(p, after_id=last, limit=100)
                rows = out.get("events") or []
                if rows:
                    last = max(e["id"] for e in rows)
                    yield {"event": "events", "data": json.dumps(rows, default=str)}
                await asyncio.sleep(EVENTS_TICK_S)
        return EventSourceResponse(gen(), ping=15)

    @r.get("/api/live/range")
    async def live_range(p=Depends(reader)) -> dict:
        """How far back stored history reaches (the scrubber offers at most the last 24 h)."""
        rg = await range_()
        now = time.time()
        first = (rg.get("samples") or {}).get("first")
        return {**rg, "now": now, "max_history_s": MAX_HISTORY_S,
                "from": max(now - MAX_HISTORY_S, first) if first else None}

    @r.get("/api/live/at")
    async def live_at(t: float, p=Depends(reader)) -> dict:
        """Topology, SLE values and traffic as stored at minute ``t`` (epoch s, floored to the minute)."""
        t = clamp_t(t) or time.time()
        t = t - t % 60 + 59.999   # the whole minute HH:MM counts as "as of HH:MM"
        t = min(t, time.time())
        st, lks, nds, sle_c, sle_f = await asyncio.gather(
            src("live_state_at", t=t), links(), nodes(),
            src("live_sle_at", site="campus", t=t), src("live_sle_at", site="fabric", t=t))
        demo = st.get("demo")
        if st.get("available") is False:
            return {"t": t, "available": False, "reason": st.get("reason"), "links": [], "nodes": [], "sles": [],
                    "traffic": {"links": []}}
        ports, ospf = st.get("ports") or {}, st.get("ospf") or {}
        site_of = {n["name"]: n["site"] for n in nds}
        link_rows = [{**link_state_at(lk, ports, ospf), "site": site_of.get(lk["a"]) or site_of.get(lk["b"])} for lk in lks]
        node_rows = [{**node_state_at(n["name"], st), "site": n["site"], "role": n["role"]} for n in nds]
        sles = []
        for site, raw in (("campus", sle_c), ("fabric", sle_f)):
            v = view.sle_summary(raw)
            if isinstance(v, dict) and v.get("available") is not False:
                for s in v.get("sles") or []:
                    sles.append({"site": site, "id": s.get("id"), "name": s.get("name"), "value_pct": s.get("value_pct"),
                                 "n": s.get("n")})
        tr = st.get("traffic") or {}
        return {"t": t, "minute": t - t % 60, "links": link_rows, "nodes": node_rows, "sles": sles,
                "sle_window_min": 60, "notes": [BFD_NOTE],
                "traffic": {"at": tr.get("at"), "speed_bps": None, "links": link_rates(lks, tr, live=False)},
                **({"demo": True} if demo else {})}

    return r
