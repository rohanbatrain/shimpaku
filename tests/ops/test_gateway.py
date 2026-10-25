"""Gateway panel client (shimpaku.gateway) against a fake LiteLLM: endpoints, masking, cache."""

from __future__ import annotations

import asyncio
import json

import httpx

from shimpaku.gateway import GatewayClient, aggregate, mask_key

ADMIN = "sk-admin-SECRETVALUE-1234"
HASHED = "88dc28d0f030c55ed4ab77ed8faf098196cb1c05df778539800c9f1243fe6b4b"
ROWS = [
    {"api_key": HASHED, "model": "ultra", "spend": 0.01, "status": "success", "startTime": "2026-10-25T10:00:00",
     "endTime": "2026-10-25T10:00:02", "total_tokens": 100, "metadata": {"user_api_key_alias": "shimpaku-daemon"}},
    {"api_key": HASHED, "model": "ultra", "spend": 0.02, "status": "failure", "startTime": "2026-10-25T10:01:00",
     "endTime": "2026-10-25T10:03:00", "metadata": {"user_api_key_alias": "shimpaku-daemon"}},
    {"api_key": "sk-raw-should-never-leak-abcd", "model": "lightning", "spend": 0.005, "status": "success",
     "startTime": "2026-10-25T10:00:00Z", "endTime": "2026-10-25T10:00:01Z", "metadata": {}},
]


def fake(calls: list):
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.url.path, dict(req.url.params), req.headers.get("authorization")))
        p = req.url.path
        if p == "/health/liveliness":
            return httpx.Response(200, json="I'm alive!")
        if p == "/health/readiness":
            return httpx.Response(200, json={"status": "healthy", "db": "connected", "litellm_version": "1.79.3"})
        if p == "/spend/logs/v2":
            return httpx.Response(200, json={"data": ROWS, "total": 3, "page": 1, "page_size": 1000, "total_pages": 1})
        if p == "/health/latest":
            return httpx.Response(200, json={"latest_health_checks": {
                "ultra": {"status": "healthy", "api_base": "https://x"}, "glm": {"status": "unhealthy"}}})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def test_without_admin_key_only_unauthenticated_health():
    calls: list = []
    out = asyncio.run(GatewayClient("https://gw.example/", None, transport=fake(calls)).get())
    assert out["connected"] is False
    assert out["health"] == {"alive": True, "ready": True, "db": "connected", "version": "1.79.3"}
    assert [c[0] for c in calls] == ["/health/liveliness", "/health/readiness"]
    assert all(c[2] is None for c in calls)
    assert "models" not in out


def test_with_admin_key_aggregates_and_never_returns_a_key():
    calls: list = []
    out = asyncio.run(GatewayClient("https://gw.example", ADMIN, transport=fake(calls)).get())
    assert "/health" not in [c[0] for c in calls]  # /health runs a test call against every model (spends)
    logs = next(c for c in calls if c[0] == "/spend/logs/v2")
    assert logs[2] == f"Bearer {ADMIN}" and logs[1]["page_size"] == "1000" and "start_date" in logs[1]
    assert next(c for c in calls if c[0] == "/health/readiness")[2] is None  # the key only where needed
    blob = json.dumps(out)
    assert ADMIN not in blob and "should-never-leak" not in blob and HASHED[:8] not in blob and "api_base" not in blob
    m = {x["name"]: x for x in out["models"]}
    assert m["ultra"]["requests"] == 2 and m["ultra"]["errors"] == 1 and m["ultra"]["error_pct"] == 50.0
    assert m["ultra"]["p50_ms"] == 2000  # failures are excluded from latency
    assert {k["name"] for k in out["keys"]} == {"shimpaku-daemon", "…abcd"}
    assert out["totals"]["requests"] == 3 and round(out["totals"]["spend_usd"], 3) == 0.035
    assert out["models_health"] == [{"model": "ultra", "healthy": True}, {"model": "glm", "healthy": False}]
    assert out["sampled"] == {"rows": 3, "total": 3, "capped": False}


def test_cached_for_30s():
    calls: list = []
    c = GatewayClient("https://gw.example", ADMIN, transport=fake(calls))
    a = asyncio.run(c.get())
    n = len(calls)
    b = asyncio.run(c.get())
    assert a["cached"] is False and b["cached"] is True and len(calls) == n


def test_gateway_down_reports_errors_without_urls_or_keys():
    def boom(req):
        raise httpx.ConnectError("connect failed to https://gw.example?key=" + ADMIN)
    out = asyncio.run(GatewayClient("https://gw.example", ADMIN, transport=httpx.MockTransport(boom)).get())
    assert out["health"]["alive"] is False and ADMIN not in json.dumps(out)
    assert out["errors"][0] == "/health/liveliness: ConnectError"


def test_mask_key():
    assert mask_key(" lease-1 ", "sk-x") == "lease-1"
    assert mask_key(None, "sk-1234567890wxyz") == "…wxyz"
    assert mask_key("", None) == "unknown"
    assert aggregate([None, {"model": "m", "spend": "bad"}])["totals"]["requests"] == 1
