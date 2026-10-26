"""LiteLLM gateway panel (D2): requests, latency, errors and spend per model and per key alias.

Endpoints, checked at source on 2026-10-25:

- ``GET /health/liveliness`` and ``GET /health/readiness`` need no key
  (docs.litellm.ai/docs/proxy/health). ``GET /health`` needs a key and *runs a test request against
  every model* (it spends), so this panel never calls it; with a key it reads ``GET /health/latest``
  (the most recent stored result, same page) instead.
- ``GET /spend/logs/v2?start_date=YYYY-MM-DD HH:MM:SS&end_date=…&page=1&page_size≤1000`` returns
  ``data``, ``total``, ``page``, ``page_size``, ``total_pages``; rows carry ``api_key`` (hashed),
  ``model``, ``spend``, ``status``, ``startTime``/``endTime`` and ``metadata.user_api_key_alias``
  (docs.litellm.ai/docs/proxy/cost_tracking). Auth: ``Authorization: Bearer <admin key>``.

The admin key is ``SHIMPAKU_LLM_ADMIN_KEY`` only. Nothing returned to the browser carries a key:
keys are shown as their alias, else ``…`` plus the last 4 characters of the gateway's hashed token.
Results are cached 30 s so a page left open does not hammer the gateway.
"""

from __future__ import annotations

from shimpaku.modelname import short_model

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

CACHE_S = 30.0
WINDOW_H = 24
PAGE_SIZE = 1000


def mask_key(alias: Any, key: Any) -> str:
    if isinstance(alias, str) and alias.strip():
        return alias.strip()[:64]
    k = str(key or "")
    return f"…{k[-4:]}" if len(k) >= 4 else "unknown"


def _ts(v: Any) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and v:
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
        except ValueError:
            return None
    return None


def _pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * (len(xs) - 1) + 0.5))], 0)


def aggregate(rows: list[dict]) -> dict:
    """Group spend-log rows by model and by masked key; never copies a key value."""
    def blank() -> dict:
        return {"requests": 0, "errors": 0, "spend_usd": 0.0, "tokens": 0, "_lat": []}

    models: dict[str, dict] = {}
    keys: dict[str, dict] = {}
    total = blank()
    for r in rows:
        if not isinstance(r, dict):
            continue
        md = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}
        model = short_model(r.get("model") or r.get("model_group") or "unknown")[:80]
        who = mask_key(md.get("user_api_key_alias") or r.get("key_alias"), r.get("api_key"))
        status = str(r.get("status") or md.get("status") or "success").lower()
        err = status not in ("success", "ok", "200")
        st, en = _ts(r.get("startTime")), _ts(r.get("endTime"))
        lat = (en - st) * 1000 if st is not None and en is not None and en >= st else None
        try:
            spend = float(r.get("spend") or 0)
        except (TypeError, ValueError):
            spend = 0.0
        tok = r.get("total_tokens") if isinstance(r.get("total_tokens"), int) else 0
        for g in (models.setdefault(model, blank()), keys.setdefault(who, blank()), total):
            g["requests"] += 1
            g["errors"] += int(err)
            g["spend_usd"] += spend
            g["tokens"] += tok
            if lat is not None and not err:
                g["_lat"].append(lat)

    def fin(name: str | None, g: dict) -> dict:
        lat = g.pop("_lat")
        out = {**g, "spend_usd": round(g["spend_usd"], 6), "p50_ms": _pct(lat, 0.5), "p95_ms": _pct(lat, 0.95),
               "error_pct": round(100 * g["errors"] / g["requests"], 1) if g["requests"] else None}
        return {"name": name, **out} if name is not None else out

    by = lambda d: sorted((fin(k, v) for k, v in d.items()), key=lambda x: (-x["spend_usd"], -x["requests"]))  # noqa: E731
    return {"totals": fin(None, total), "models": by(models), "keys": by(keys)}


def _health_summary(h: Any) -> list[dict] | None:
    """/health/latest (or a /health-shaped body) → [{model, healthy}] without api_base or keys."""
    if not isinstance(h, dict):
        return None
    body = h.get("health_check_results") if isinstance(h.get("health_check_results"), dict) else h
    if isinstance(body.get("latest_health_checks"), dict):
        return [{"model": short_model(m)[:80], "healthy": str((v or {}).get("status", "")).lower() == "healthy"}
                for m, v in body["latest_health_checks"].items()]
    if "healthy_endpoints" not in body and "unhealthy_endpoints" not in body:
        return None
    out = []
    for ok, k in ((True, "healthy_endpoints"), (False, "unhealthy_endpoints")):
        for e in body.get(k) or []:
            if isinstance(e, dict):
                out.append({"model": short_model(e.get("model") or "unknown")[:80], "healthy": ok})
    return out


class GatewayClient:
    def __init__(self, base: str, admin_key: str | None, *, transport: httpx.AsyncBaseTransport | None = None,
                 timeout_s: float = 10.0, cache_s: float = CACHE_S):
        self.base = base.rstrip("/")
        self._admin = admin_key or None
        self._transport, self.timeout_s, self.cache_s = transport, timeout_s, cache_s
        self._cache: tuple[float, dict] | None = None
        self._lock = asyncio.Lock()

    def __repr__(self) -> str:
        return f"GatewayClient(base={self.base!r}, admin={'set' if self._admin else 'unset'})"

    async def _get(self, c: httpx.AsyncClient, path: str, params: dict | None = None, auth: bool = False) -> tuple[Any, str | None]:
        headers = {"Authorization": f"Bearer {self._admin}"} if auth and self._admin else {}
        try:
            r = await c.get(self.base + path, params=params, headers=headers)
        except httpx.HTTPError as e:
            return None, f"{path}: {type(e).__name__}"  # never the URL with params or a header
        if r.status_code >= 400:
            return None, f"{path}: HTTP {r.status_code}"
        try:
            return r.json(), None
        except ValueError:
            return r.text[:200], None

    async def fetch(self) -> dict:
        now = time.time()
        t1 = datetime.now(timezone.utc)
        t0 = t1 - timedelta(hours=WINDOW_H)
        out: dict[str, Any] = {"demo": False, "connected": self._admin is not None, "fetched_at": now,
                               "window_h": WINDOW_H, "errors": []}
        async with httpx.AsyncClient(transport=self._transport, timeout=self.timeout_s) as c:
            live, e1 = await self._get(c, "/health/liveliness")
            ready, e2 = await self._get(c, "/health/readiness")
            out["health"] = {"alive": e1 is None, "ready": e2 is None and isinstance(ready, dict)
                             and ready.get("status") == "healthy",
                             "db": ready.get("db") if isinstance(ready, dict) else None,
                             "version": ready.get("litellm_version") if isinstance(ready, dict) else None}
            out["errors"] += [e for e in (e1, e2) if e]
            if not self._admin:
                return out
            fmt = "%Y-%m-%d %H:%M:%S"
            logs, e3 = await self._get(c, "/spend/logs/v2", {"start_date": t0.strftime(fmt), "end_date": t1.strftime(fmt),
                                                            "page": 1, "page_size": PAGE_SIZE}, auth=True)
            latest, e4 = await self._get(c, "/health/latest", auth=True)
        out["errors"] += [e for e in (e3, e4) if e]
        rows = logs.get("data") if isinstance(logs, dict) else logs if isinstance(logs, list) else None
        if isinstance(rows, list):
            out.update(aggregate(rows))
            total = logs.get("total") if isinstance(logs, dict) else None
            out["sampled"] = {"rows": len(rows), "total": total if isinstance(total, int) else len(rows),
                              "capped": isinstance(total, int) and total > len(rows)}
        out["models_health"] = _health_summary(latest)
        return out

    async def get(self) -> dict:
        async with self._lock:
            if self._cache and time.time() - self._cache[0] < self.cache_s:
                return {**self._cache[1], "cached": True}
            data = await self.fetch()
            self._cache = (time.time(), data)
            return {**data, "cached": False}
