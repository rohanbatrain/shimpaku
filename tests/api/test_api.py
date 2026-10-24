"""API auth, CSRF, token scopes and approval rules (demo service, no lab)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from shimpaku.api import demo
from shimpaku.api.app import create_app
from shimpaku.api.auth import hash_password

PW = "correct horse battery staple"
BASE = "http://testserver"


@pytest.fixture(autouse=True)
def _reset_demo():
    demo._approved.clear()
    demo._cancelled.clear()
    yield


def csrf_headers(c: TestClient) -> dict:
    c.get("/api/whoami")
    tok = c.cookies.get("shimpaku_csrf") or c.cookies.get("__Host-shimpaku_csrf")
    return {"X-CSRF-Token": tok, "Origin": BASE}


@pytest.fixture
def off():
    return TestClient(create_app(demo, auth_mode="off"))


@pytest.fixture
def local(monkeypatch):
    monkeypatch.setenv("SHIMPAKU_ADMIN_PASSWORD_HASH", hash_password(PW))
    return TestClient(create_app(demo, auth_mode="local"))


def login(c: TestClient) -> dict:
    h = csrf_headers(c)
    r = c.post("/api/login", json={"password": PW}, headers=h)
    assert r.status_code == 200, r.text
    return csrf_headers(c)


def make_token(c: TestClient, h: dict, scope: str) -> str:
    r = c.post("/api/tokens", json={"name": f"agent-{scope}", "scope": scope}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["token"]


# ---- auth off ---------------------------------------------------------------------
def test_off_reads_without_login(off):
    assert off.get("/api/sle?site=campus").status_code == 200
    who = off.get("/api/whoami").json()
    assert who["mode"] == "off" and who["user"] == "operator" and who["can_approve"]


def test_off_still_needs_csrf_for_writes(off):
    assert off.post("/api/actions/42/approve").status_code == 403
    assert off.post("/api/actions/42/approve", headers=csrf_headers(off)).status_code == 200


def test_off_login_is_refused(off):
    assert off.post("/api/login", json={"password": "x"}, headers=csrf_headers(off)).status_code == 400


# ---- auth local -------------------------------------------------------------------
def test_local_requires_login(local):
    assert local.get("/api/sle").status_code == 401
    assert local.get("/api/whoami").json()["authenticated"] is False


def test_local_wrong_password(local):
    r = local.post("/api/login", json={"password": "nope"}, headers=csrf_headers(local))
    assert r.status_code == 401


def test_local_login_cookie_flags_over_http(local):
    h = csrf_headers(local)
    r = local.post("/api/login", json={"password": PW}, headers=h)
    sc = r.headers.get_list("set-cookie")
    sess = next(x for x in sc if x.startswith("shimpaku_session="))
    assert "HttpOnly" in sess and "SameSite=lax" in sess and "Path=/" in sess and "Secure" not in sess
    assert local.get("/api/sle").status_code == 200
    st = local.get("/api/settings").json()
    assert st["cookie_secure"] is False and st["cookie_warning"]


def test_local_https_uses_host_prefix(monkeypatch):
    monkeypatch.setenv("SHIMPAKU_ADMIN_PASSWORD_HASH", hash_password(PW))
    c = TestClient(create_app(demo, auth_mode="local"), base_url="https://testserver")
    c.get("/api/whoami")
    tok = c.cookies.get("__Host-shimpaku_csrf")
    r = c.post("/api/login", json={"password": PW}, headers={"X-CSRF-Token": tok, "Origin": "https://testserver"})
    assert r.status_code == 200
    sess = next(x for x in r.headers.get_list("set-cookie") if x.startswith("__Host-shimpaku_session="))
    assert "Secure" in sess and "Path=/" in sess and "Domain" not in sess


def test_logout_ends_session(local):
    h = login(local)
    assert local.post("/api/logout", headers=h).status_code == 200
    assert local.get("/api/sle").status_code == 401


def test_login_rate_limited(local):
    h = csrf_headers(local)
    codes = [local.post("/api/login", json={"password": "bad"}, headers=h).status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429


# ---- CSRF ---------------------------------------------------------------------------
def test_csrf_missing_header(local):
    h = login(local)
    assert local.post("/api/actions/42/approve", headers={"Origin": BASE}).status_code == 403
    assert local.post("/api/actions/42/approve", headers={**h, "X-CSRF-Token": "wrong"}).status_code == 403


def test_csrf_foreign_origin(local):
    h = login(local)
    assert local.post("/api/actions/42/approve", headers={**h, "Origin": "https://evil.example"}).status_code == 403
    no_origin = {"X-CSRF-Token": h["X-CSRF-Token"], "Sec-Fetch-Site": "cross-site"}
    assert local.post("/api/actions/42/approve", headers=no_origin).status_code == 403


def test_session_user_approves(local):
    h = login(local)
    r = local.post("/api/actions/42/approve", headers=h)
    assert r.status_code == 200 and r.json()["approved_by"] == "admin"
    assert local.post("/api/actions/42/approve", headers=h).status_code == 409  # no longer awaiting approval


# ---- tokens -------------------------------------------------------------------------
def test_token_scopes(local):
    h = login(local)
    read_tok, prop_tok = make_token(local, h, "read"), make_token(local, h, "propose")
    agent = TestClient(local.app)  # no cookies
    rd = {"Authorization": f"Bearer {read_tok}"}
    pr = {"Authorization": f"Bearer {prop_tok}"}
    assert agent.get("/api/incidents", headers=rd).status_code == 200
    assert agent.post("/api/actions", json={"kind": "bounce_port", "args": {}}, headers=rd).status_code == 403
    assert agent.post("/api/actions", json={"kind": "bounce_port", "args": {}}, headers=pr).status_code == 201
    assert agent.post("/api/actions/41/cancel", headers=rd).status_code == 403
    assert agent.post("/api/actions/41/cancel", headers=pr).status_code == 200


def test_tokens_never_approve(local):
    h = login(local)
    agent = TestClient(local.app)
    for scope in ("read", "propose"):
        tok = make_token(local, h, scope)
        assert agent.post("/api/actions/42/approve", headers={"Authorization": f"Bearer {tok}"}).status_code == 403
    assert demo._approved == {}


def test_no_approve_scope(local):
    h = login(local)
    assert local.post("/api/tokens", json={"name": "x", "scope": "approve"}, headers=h).status_code == 422


def test_tokens_cannot_manage_tokens(local):
    h = login(local)
    tok = make_token(local, h, "propose")
    agent = TestClient(local.app)
    assert agent.get("/api/tokens", headers={"Authorization": f"Bearer {tok}"}).status_code == 403


def test_revoked_and_bad_tokens(local):
    h = login(local)
    tok = make_token(local, h, "read")
    tid = local.get("/api/tokens").json()["tokens"][0]["id"]
    assert local.delete(f"/api/tokens/{tid}", headers=h).status_code == 200
    agent = TestClient(local.app)
    assert agent.get("/api/sle", headers={"Authorization": f"Bearer {tok}"}).status_code == 401
    assert agent.get("/api/sle", headers={"Authorization": "Bearer shp_nope"}).status_code == 401


def test_tokens_stored_hashed(local, tmp_path):
    h = login(local)
    tok = make_token(local, h, "read")
    rows = local.app.state.auth._db.execute("SELECT token_hash FROM token").fetchall()
    assert all(tok not in r[0] for r in rows)
    listed = local.get("/api/tokens").json()["tokens"]
    assert all("token" not in t and "hash" not in t for t in listed)


def test_token_in_off_mode_still_cannot_approve(off):
    h = csrf_headers(off)
    tok = off.post("/api/tokens", json={"name": "a", "scope": "propose"}, headers=h).json()["token"]
    agent = TestClient(off.app)
    assert agent.post("/api/actions/42/approve", headers={"Authorization": f"Bearer {tok}"}).status_code == 403


# ---- routes ---------------------------------------------------------------------------
def test_contract_get_routes(off):
    for url in ["/api/inventory", "/api/sle?site=fabric&window=1440", "/api/incidents", "/api/incidents/INC-0107",
                "/api/incidents/INC-0107/blast-radius", "/api/actions", "/api/actions/41", "/api/audit",
                "/api/topology", "/api/bench/results", "/api/whoami"]:
        r = off.get(url)
        assert r.status_code == 200, url
    assert off.get("/api/incidents/NOPE").status_code == 404


def test_assistant_streams(off):
    with off.stream("POST", "/api/assistant/ask", json={"question": "how is the fabric?"}, headers=csrf_headers(off)) as r:
        assert r.status_code == 200
        body = "".join(r.iter_text())
    assert "event: tool" in body and "event: evidence" in body and "event: done" in body


def test_missing_service_function_is_501():
    class Empty:
        pass
    c = TestClient(create_app(Empty(), auth_mode="off"))
    assert c.get("/api/bench/results").status_code == 501


def test_security_headers(off):
    r = off.get("/api/whoami")
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"


def test_service_error_dicts_map_to_http():
    class Svc:
        async def action_get(self, action_id):
            return {"error": {"code": "not_found", "message": "no action"}}

        async def action_cancel(self, action_id, caller):
            return {"error": {"code": "bad_state", "message": "running"}}

        async def sle_get_summary(self, site, window_min=60):
            return {"available": False, "reason": "no provider"}

    c = TestClient(create_app(Svc(), auth_mode="off"))
    assert c.get("/api/actions/9").status_code == 404
    assert c.post("/api/actions/9/cancel", headers=csrf_headers(c)).status_code == 409
    assert c.get("/api/sle").json()["available"] is False


def test_approve_passes_dashboard_caller(local):
    seen = {}

    async def approve(action_id, user):
        seen["user"] = user
        return {"id": action_id}

    local.app.state.service.action_approve, orig = approve, demo.action_approve
    try:
        h = login(local)
        assert local.post("/api/actions/42/approve", headers=h).status_code == 200
    finally:
        demo.action_approve = orig
    assert seen["user"]["kind"] == "dashboard" and seen["user"]["name"] == "admin"


def test_reject_needs_session(local):
    h = login(local)
    tok = make_token(local, h, "propose")
    agent = TestClient(local.app)
    assert agent.post("/api/actions/42/reject", headers={"Authorization": f"Bearer {tok}"}).status_code == 403
    assert local.post("/api/actions/42/reject", headers=h).status_code == 200
