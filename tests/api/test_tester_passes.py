"""Build step 4 (docs/DEPLOY-DESIGN.md): invites, tester sessions, leases, the queue, end-of-lease
cleanup, restart recovery and retention. Fakes only: no lab, no gateway."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "actions"))
from test_engine import box, make_engine  # noqa: E402,F401

from shimpaku import service  # noqa: E402
from shimpaku.actions import engine as engmod  # noqa: E402
from shimpaku.actions import fixture  # noqa: E402
from shimpaku.actions import policy as P  # noqa: E402
from shimpaku.api.app import create_app  # noqa: E402
from shimpaku.api.totp import TotpStore, b32_key  # noqa: E402
from shimpaku.api.totp import totp as totp_code  # noqa: E402
from shimpaku.api.auth import AuthStore, hash_password  # noqa: E402
from shimpaku.api.lease import LeaseManager  # noqa: E402
from shimpaku.api.llm_keys import LiteLLMKeys  # noqa: E402
from shimpaku.api.ratelimit import RateLimiter  # noqa: E402
from shimpaku.mcp.audit import AuditLog  # noqa: E402

PW = "correct horse battery staple"
GW = "http://gateway.test"
NOTE = "Jane Doe, met at a LinkedIn recruiter call"
NAME = "Jane"


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


class Seeder:
    """service stand-in for the lease manager: records demo_seed calls; can be made to fail."""

    def __init__(self):
        self.calls: list[dict] = []
        self.fail = False

    async def demo_seed(self, caller):
        self.calls.append(caller)
        if self.fail:
            return {"error": {"code": "busy", "message": "PendingConfirm"}}
        return {"op": "seed", "changed": True, "writes": []}


@pytest.fixture
def world(tmp_path, monkeypatch, box):  # noqa: F811
    monkeypatch.setenv("SHIMPAKU_ADMIN_PASSWORD_HASH", hash_password(PW))
    from shimpaku.config import settings
    monkeypatch.setattr(settings, "public_url", "https://demo.example")
    e = make_engine(tmp_path, box)

    def facts_fn(kind, args):
        return P.Facts(known_devices={"sw1", "leaf2"}, topology_role="unused", pending_confirm=False,
                       vlan_seen_elsewhere=True,
                       port={"live_role": "trunk", "interface_range_lines": [], "lldp_neighbor": None,
                             "config_own": "", "config_inherited": ""},
                       open_actions_device=e.store.count_open(args.get("device")),
                       open_actions_total=e.store.count_open())
    e.facts_fn = facts_fn
    engmod.set_engine(e)
    audit = AuditLog(tmp_path / "audit.db", tmp_path / "audit.jsonl")
    service.set_audit_log(audit)
    clock = Clock()
    seeder = Seeder()
    auth = AuthStore(tmp_path / "auth.db")
    auth.clock = clock
    keys = LiteLLMKeys(GW, "sk-admin-MASTER", budget_usd=0.5, rpm=10, models=["m1", "m2"])
    lm = LeaseManager(auth, seeder, keys, lease_s=1800, max_active=1, demo_ops=5, clock=clock,
                      action_store=lambda: e.store)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>spa</title>")
    totp = TotpStore(tmp_path / "totp.json")
    totp_secret = totp.enable()["secret"]
    app = create_app(service, auth_mode="local", web_dist=dist, leases=lm,
                     limiter=RateLimiter({"write": (1000, 1000)}), totp=totp)
    assert app.state.auth is auth  # the app shares the lease manager's store
    yield type("W", (), dict(app=app, lm=lm, auth=auth, clock=clock, seeder=seeder, eng=e, audit=audit,
                             tmp=tmp_path, keys=keys,
                             totp=totp, totp_secret=totp_secret))
    service.set_audit_log(None)
    engmod.set_engine(None)


def client(w, https=False) -> TestClient:
    return TestClient(w.app, base_url="https://testserver" if https else "http://testserver")


def csrf(c: TestClient) -> dict:
    c.get("/api/whoami")
    tok = c.cookies.get("shimpaku_csrf") or c.cookies.get("__Host-shimpaku_csrf")
    return {"X-CSRF-Token": tok, "Origin": str(c.base_url).rstrip("/")}


def otp(w) -> str:
    """A fresh TOTP code: each call moves the TOTP clock one step on (a code is accepted once)."""
    w.totp_t = getattr(w, "totp_t", w.clock.t) + 30
    w.totp.clock = lambda: w.totp_t
    return totp_code(b32_key(w.totp_secret), w.totp_t)


def operator(w) -> tuple[TestClient, dict]:
    c = client(w)
    r = c.post("/api/login", json={"password": PW, "code": otp(w)}, headers=csrf(c))
    assert r.status_code == 200
    return c, csrf(c)


def invite(w, note=NOTE) -> str:
    c, h = operator(w)
    r = c.post("/api/admin/invites", json={"note": note}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["token"]


def redeem(w, token, *, consent=True, name=NAME, https=False) -> tuple[TestClient, dict]:
    c = client(w, https)
    r = c.post("/api/invites/redeem", json={"token": token, "consent": consent, "display_name": name},
               headers=csrf(c))
    return c, r


# ---- invites -------------------------------------------------------------------------------------

def test_invite_stores_only_the_sha256(world):
    tok = invite(world)
    dump = "\n".join(world.auth.db.iterdump())
    assert tok not in dump
    assert hashlib.sha256(tok.encode()).hexdigest() in dump
    assert len(tok) >= 43  # 32 random bytes, urlsafe base64


def test_invite_link_uses_the_configured_base_not_the_host_header(world):
    c, h = operator(world)
    r = c.post("/api/admin/invites", json={"note": "x"},
               headers={**h, "Host": "evil.example", "Origin": "http://evil.example"})
    assert r.status_code == 201, r.text
    assert r.json()["link"].startswith("https://demo.example/start?i=")


def test_get_start_and_status_do_not_consume(world):
    tok = invite(world)
    c = client(world)
    for _ in range(3):
        r = c.get(f"/start?i={tok}")
        assert r.status_code == 200 and r.headers["referrer-policy"] == "no-referrer"
        s = c.get(f"/api/invites/status?i={tok}")
        assert s.json()["valid"] is True and s.headers["referrer-policy"] == "no-referrer"
        assert s.json()["retention_days"] == 90
    _, r = redeem(world, tok)
    assert r.status_code == 200, r.text


def test_invite_is_single_use(world):
    tok = invite(world)
    assert redeem(world, tok)[1].status_code == 200
    _, r = redeem(world, tok)
    assert r.status_code == 410 and "already been used" in r.json()["detail"]
    assert client(world).get(f"/api/invites/status?i={tok}").json() == {
        "valid": False, "reason": "used", "retention_days": 90, "lease_minutes": 30,
        "contact": __import__("shimpaku.config", fromlist=["settings"]).settings.privacy_contact}


def test_invite_expires(world):
    tok = invite(world)
    world.clock.t += 72 * 3600 + 1
    _, r = redeem(world, tok)
    assert r.status_code == 410 and "expired" in r.json()["detail"]


def test_revoked_invite_is_refused(world):
    tok = invite(world)
    iid = world.lm.list_invites()[0]["id"]
    c, h = operator(world)
    assert c.delete(f"/api/admin/invites/{iid}", headers=h).status_code == 200
    assert redeem(world, tok)[1].status_code == 410


def test_consent_is_required(world):
    tok = invite(world)
    _, r = redeem(world, tok, consent=False)
    assert r.status_code == 422
    assert redeem(world, tok)[1].status_code == 200  # not consumed by the refusal


def test_redeem_needs_csrf(world):
    tok = invite(world)
    c = client(world)
    c.get("/api/whoami")
    assert c.post("/api/invites/redeem", json={"token": tok, "consent": True}).status_code == 403
    h = csrf(c)
    assert c.post("/api/invites/redeem", json={"token": tok, "consent": True},
                  headers={**h, "Origin": "https://evil.example"}).status_code == 403
    assert c.post("/api/invites/redeem", json={"token": tok, "consent": True}, headers=h).status_code == 200


def test_redeem_gives_a_host_prefixed_tester_session_over_https(world):
    tok = invite(world)
    c, r = redeem(world, tok, https=True)
    assert r.status_code == 200
    sc = [x for x in r.headers.get_list("set-cookie") if x.startswith("__Host-shimpaku_session=")]
    assert sc and "Secure" in sc[0] and "HttpOnly" in sc[0] and "Path=/" in sc[0]
    who = c.get("/api/whoami").json()
    assert who["role"] == "tester" and who["user"].startswith("tester:inv_") and NAME not in who["user"]


def test_invite_admin_surfaces_are_operator_only(world):
    tok = invite(world)
    c, _ = redeem(world, tok)
    h = csrf(c)
    assert c.get("/api/admin/invites").status_code == 403
    assert c.post("/api/admin/invites", json={"note": "x"}, headers=h).status_code == 403
    assert c.get("/api/tokens").status_code == 403
    assert c.post("/api/tokens", json={"name": "x", "scope": "read"}, headers=h).status_code == 403
    assert client(world).get("/api/admin/invites").status_code == 401


# ---- leases and the queue ------------------------------------------------------------------------

def test_one_active_lease_and_a_fifo_queue(world):
    a, _ = redeem(world, invite(world))
    b, _ = redeem(world, invite(world))
    c, _ = redeem(world, invite(world))
    la, lb, lc = (x.get("/api/lease").json()["lease"] for x in (a, b, c))
    assert la["state"] == "active" and la["remaining_s"] == 1800
    assert (lb["state"], lb["queue_position"]) == ("queued", 1)
    assert (lc["state"], lc["queue_position"]) == ("queued", 2)
    # a queued tester cannot write
    hb = csrf(b)
    r = b.post("/api/actions", json={"kind": fixture.KIND, "args": fixture.ARGS, "reason": "x"}, headers=hb)
    assert r.status_code == 403 and "queue position 1" in r.json()["detail"]
    assert b.post("/api/demo/break", headers=hb).status_code == 403
    # the active tester ends the session: b is promoted, c moves up
    assert a.post("/api/lease/end", headers=csrf(a)).status_code == 200
    assert a.get("/api/lease").status_code == 401  # the session is gone
    assert b.get("/api/lease").json()["lease"]["state"] == "active"
    assert c.get("/api/lease").json()["lease"]["queue_position"] == 1


def test_lease_times_out(world):
    a, _ = redeem(world, invite(world))
    b, _ = redeem(world, invite(world))
    la = a.get("/api/lease").json()["lease"]["id"]
    world.clock.t += 1801
    assert a.get("/api/lease").status_code == 401  # expired at once, before the timer runs
    import asyncio
    asyncio.run(world.lm.tick())
    assert world.lm.lease(la)["end_reason"] == "timeout"
    assert b.get("/api/lease").json()["lease"]["state"] == "active"


def test_lease_survives_a_daemon_restart(world):
    a, _ = redeem(world, invite(world))
    b, _ = redeem(world, invite(world))
    la = a.get("/api/lease").json()["lease"]["id"]
    world.seeder.fail = True
    world.clock.t += 1801
    import asyncio
    asyncio.run(world.lm.tick())  # timeout: cleanup starts, the re-seed fails (lab busy)
    assert world.lm.lease(la)["state"] == "ending"
    # "restart": new store objects on the same state.db
    auth2 = AuthStore(world.tmp / "auth.db")
    world.seeder.fail = False
    lm2 = LeaseManager(auth2, world.seeder, None, lease_s=1800, clock=world.clock, action_store=lambda: world.eng.store)
    assert lm2.lease(la)["state"] == "ending"
    asyncio.run(lm2.tick())
    assert lm2.lease(la)["state"] == "ended"
    lb = [x for x in lm2.leases() if x["id"] != la][0]
    assert lb["state"] == "active"
    assert world.seeder.calls[-1]["role"] == "system" and world.seeder.calls[-1]["lease_id"] == la


@respx.mock
def test_lease_end_cancels_actions_revokes_token_and_key_and_reseeds(world):
    gen = respx.post(f"{GW}/key/generate").mock(return_value=httpx.Response(200, json={"key": "sk-lease-123"}))
    dele = respx.post(f"{GW}/key/delete").mock(return_value=httpx.Response(200, json={"deleted_keys": ["x"]}))
    t, _ = redeem(world, invite(world))
    h = csrf(t)
    lease_id = t.get("/api/lease").json()["lease"]["id"]
    a = t.post("/api/actions", json={"kind": fixture.KIND, "args": fixture.ARGS, "reason": "fix"}, headers=h).json()
    assert a["status"] == "awaiting_approval", a
    tok = t.post("/api/lease/token", headers=h).json()["token"]
    assert t.post("/api/lease/token", headers=h).status_code == 409  # one per lease
    bearer = {"Authorization": f"Bearer {tok}"}
    assert client(world).get("/api/actions", headers=bearer).status_code == 200
    import asyncio
    assert asyncio.run(world.lm.llm_key(lease_id)) == "sk-lease-123"
    body = json.loads(gen.calls[0].request.content)
    assert gen.calls[0].request.headers["authorization"] == "Bearer sk-admin-MASTER"
    # the operator revokes the lease
    c, oh = operator(world)
    assert c.post(f"/api/admin/leases/{lease_id}/revoke", headers=oh).status_code == 200
    assert world.eng.store.get(a["id"])["status"] == "cancelled"
    assert client(world).get("/api/actions", headers=bearer).status_code == 401
    assert json.loads(dele.calls[0].request.content) == {"keys": ["sk-lease-123"]}
    assert world.seeder.calls and world.seeder.calls[-1]["role"] == "system"
    assert world.lm.lease(lease_id)["state"] == "ended"
    assert body["key_alias"] == f"shimpaku-lease-{lease_id}"
    rows = world.audit.query(action_id=a["id"])
    assert any(r["tool"] == "action_cancel" and r["actor"]["role"] == "system" for r in rows)


def test_tester_token_is_scoped_propose_tester_and_expires_with_the_lease(world):
    t, _ = redeem(world, invite(world))
    tok = t.post("/api/lease/token", headers=csrf(t)).json()
    assert tok["scope"] == "propose" and tok["mcp_url"] == "https://demo.example/mcp"
    rec = world.lm.verify_token(tok["token"])
    assert rec["role"] == "tester" and rec["lease_id"] and rec["expires"] == world.lm.lease(rec["lease_id"])["ends"]
    bearer = {"Authorization": f"Bearer {tok['token']}"}
    r = client(world).post("/api/actions", json={"kind": "bounce_port", "args": {"device": "leaf2", "ifname": "ge-0/0/3"},
                                                 "reason": "x"}, headers=bearer)
    assert r.json()["status"] == "blocked" and r.json()["policy"]["reasons"][0].startswith("T0 tester scope")
    world.clock.t += 1801
    assert world.lm.verify_token(tok["token"]) is None
    assert client(world).get("/api/actions", headers=bearer).status_code == 401


def test_demo_routes_tester_with_lease_limit_and_operator(world, monkeypatch):
    calls = []

    async def fake(op):
        async def f(caller):
            calls.append((op, caller["role"], caller["lease_id"]))
            return {"op": op, "changed": True, "writes": []}
        return f
    import asyncio
    monkeypatch.setattr(service, "demo_break", asyncio.run(fake("break")))
    monkeypatch.setattr(service, "demo_seed", asyncio.run(fake("seed")))
    t, _ = redeem(world, invite(world))
    h = csrf(t)
    for _ in range(5):
        assert t.post("/api/demo/break", headers=h).status_code == 200
    r = t.post("/api/demo/break", headers=h)
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    assert t.post("/api/demo/seed", headers=h).status_code == 200
    assert t.get("/api/lease").json()["lease"]["breaks_left"] == 0
    c, oh = operator(world)
    assert c.post("/api/demo/seed", headers=oh).status_code == 200
    assert calls[0][1] == "tester" and calls[0][2] and calls[-1][1] == "operator"
    tok = c.post("/api/tokens", json={"name": "agent", "scope": "propose"}, headers=oh).json()["token"]
    assert client(world).post("/api/demo/break", headers={"Authorization": f"Bearer {tok}"}).status_code == 403


def test_tester_approves_own_fixture_action_only(world):
    t, _ = redeem(world, invite(world))
    h = csrf(t)
    a = t.post("/api/actions", json={"kind": fixture.KIND, "args": fixture.ARGS, "reason": "fix"}, headers=h).json()
    c, oh = operator(world)
    other = c.post("/api/actions", json={"kind": "bounce_port", "args": {"device": "leaf2", "ifname": "ge-0/0/3"},
                                         "reason": "x"}, headers=oh).json()
    assert t.post(f"/api/actions/{other['id']}/approve", headers=h).status_code == 403
    assert t.post(f"/api/actions/{a['id']}/approve", headers=h).json()["status"] == "approved"


# ---- retention -----------------------------------------------------------------------------------

def test_purge_deletes_identity_data_and_audit_still_verifies(world):
    t, _ = redeem(world, invite(world))
    t.post("/api/actions", json={"kind": fixture.KIND, "args": fixture.ARGS, "reason": "fix"}, headers=csrf(t))
    t.post("/api/lease/end", headers=csrf(t))
    unredeemed = invite(world, note="Never used, Bob")
    assert unredeemed
    audit_blob = json.dumps(world.audit.query(limit=1000))
    assert NOTE not in audit_blob and NAME not in audit_blob and "Bob" not in audit_blob  # never audited
    assert world.lm.purge() == {"leases": 0, "invites": 0, "requests": 0}  # inside the retention period
    world.clock.t += 90 * 86400 + 1
    assert world.lm.purge() == {"leases": 1, "invites": 1, "requests": 0}  # the unredeemed one: 90 d after it expires
    world.clock.t += 72 * 3600
    assert world.lm.purge() == {"leases": 0, "invites": 1, "requests": 0}
    dump = "\n".join(world.auth.db.iterdump())
    assert NOTE not in dump and "Never used" not in dump and f"'{NAME}'" not in dump
    assert all(i["note"] is None for i in world.lm.list_invites())
    assert world.audit.verify()["ok"]


def test_cli_invite_needs_a_public_url(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from shimpaku.api.cli_admin import admin_app
    from shimpaku.config import settings
    monkeypatch.setattr(settings, "data", tmp_path)
    monkeypatch.setattr(settings, "public_url", None)
    monkeypatch.setattr(service, "_audit", None)
    r = CliRunner().invoke(admin_app, ["invite", "create", "--note", "x"])
    assert r.exit_code == 2 and "SHIMPAKU_PUBLIC_URL" in r.output
    monkeypatch.setattr(settings, "public_url", "https://demo.example/")
    r = CliRunner().invoke(admin_app, ["invite", "create", "--note", "x"])
    assert r.exit_code == 0 and "https://demo.example/start?i=" in r.output
    r = CliRunner().invoke(admin_app, ["invite", "list"])
    assert "open" in r.output
    service.set_audit_log(None)
