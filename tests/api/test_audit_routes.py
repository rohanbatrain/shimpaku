"""The audit API passes its filters through and exposes the hash-chain verifier."""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from shimpaku.api.app import create_app


def _client(seen: dict) -> TestClient:
    async def audit_query(**kw):
        seen.update(kw)
        return {"rows": []}

    async def audit_verify():
        return {"ok": True, "rows": 17, "first_bad": None}

    svc = SimpleNamespace(audit_query=audit_query, audit_verify=audit_verify)
    return TestClient(create_app(svc, auth_mode="off"))


def test_audit_filters_reach_the_service():
    seen: dict = {}
    r = _client(seen).get("/api/audit", params={"action_id": "act-1", "tool": "executor", "limit": 5})
    assert r.status_code == 200
    assert seen == {"tool": "executor", "action_id": "act-1", "status": None, "limit": 5}


def test_audit_verify_endpoint():
    r = _client({}).get("/api/audit/verify")
    assert r.status_code == 200 and r.json()["ok"] is True
