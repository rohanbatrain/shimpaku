"""Build step 6 (docs/DEPLOY-DESIGN.md): red team of everything a tester or an anonymous visitor
can reach, in-process with fakes (TestClient, the MCP server in memory, fake devices). No lab.

Each test is an attack; it passes when the attack fails. Attacks that succeeded on the code as it
was on 2026-10-24 are marked ``REGRESSION`` with the commit that fixed them.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT / "actions"))
from test_engine import box  # noqa: E402,F401
from test_tester_passes import GW, NAME, NOTE, PW, client, csrf, invite, operator, redeem, world  # noqa: E402,F401

from shimpaku import service  # noqa: E402
from shimpaku.actions import fixture  # noqa: E402
from shimpaku.actions.engine import Caller  # noqa: E402
from shimpaku.api.app import create_app  # noqa: E402
from shimpaku.api.lease import LeaseManager
from shimpaku.api.lease import tester_name as _tname  # noqa: E402
from shimpaku.api.llm_keys import LiteLLMKeys  # noqa: E402
from shimpaku.api.ratelimit import RateLimiter  # noqa: E402
from shimpaku.mcp import server as mcp_mod  # noqa: E402

FIX = {"kind": fixture.KIND, "args": dict(fixture.ARGS), "reason": "fix"}
OTHER = {"kind": "bounce_port", "args": {"device": "leaf2", "ifname": "ge-0/0/3"}, "reason": "x"}


def as_tester(w, https=False):
    c, r = redeem(w, invite(w), https=https)
    assert r.status_code == 200, r.text
    return c, csrf(c), r.json()["lease"]


def other_lease_action(w, lease_id: str) -> dict:
    """A fixture action created under `lease_id` straight through the engine (another tester's)."""
    return w.eng.propose(fixture.KIND, dict(fixture.ARGS), "theirs",
                         Caller(kind="dashboard", name="tester:inv_other", role="tester", lease_id=lease_id))


# ================================================================ privilege

@pytest.mark.parametrize("verb", ["approve", "reject", "cancel"])
def test_tester_cannot_decide_an_operators_action(world, verb):
    t, h, _ = as_tester(world)
    c, oh = operator(world)
    a = c.post("/api/actions", json=FIX, headers=oh).json()  # the fixture itself, but the operator's
    assert a["status"] == "awaiting_approval", a
    assert t.post(f"/api/actions/{a['id']}/{verb}", headers=h).status_code == 403
    assert world.eng.get(a["id"])["status"] == "awaiting_approval"


@pytest.mark.parametrize("verb", ["approve", "reject", "cancel"])
def test_tester_cannot_decide_another_leases_fixture_action(world, verb):
    a = other_lease_action(world, "lse_someone_else")
    assert a["status"] == "awaiting_approval", a
    t, h, _ = as_tester(world)
    assert t.post(f"/api/actions/{a['id']}/{verb}", headers=h).status_code == 403
    assert world.eng.get(a["id"])["status"] == "awaiting_approval"


def test_tester_token_never_approves_or_rejects_even_its_own_action(world):
    t, h, _ = as_tester(world)
    tok = t.post("/api/lease/token", headers=h).json()["token"]
    bearer = {"Authorization": f"Bearer {tok}"}
    a = client(world).post("/api/actions", json=FIX, headers=bearer).json()
    assert a["status"] == "awaiting_approval"
    for verb in ("approve", "reject"):
        assert client(world).post(f"/api/actions/{a['id']}/{verb}", headers=bearer).status_code == 403
    assert world.eng.get(a["id"])["status"] == "awaiting_approval"


@pytest.mark.parametrize("body", [
    OTHER,
    {"kind": "disable_port", "args": {"device": "leaf2", "ifname": "ge-0/0/3"}, "reason": "x"},
    {"kind": "rerun_deploy", "args": {"topology": "campus.yml"}, "reason": "x"},
    {"kind": "rollback", "args": {"device": "sw1"}, "reason": "x"},
    {"kind": fixture.KIND, "args": {"device": "sw1", "ifname": "ge-0/0/5", "vlan": 998}, "reason": "x"},
    {"kind": fixture.KIND, "args": {"device": "leaf2", "ifname": "ge-0/0/5", "vlan": 999}, "reason": "x"},
])
def test_tester_session_and_token_cannot_propose_beyond_the_fixture(world, body):
    t, h, _ = as_tester(world)
    tok = t.post("/api/lease/token", headers=h).json()["token"]
    for r in (t.post("/api/actions", json=body, headers=h),
              client(world).post("/api/actions", json=body, headers={"Authorization": f"Bearer {tok}"})):
        assert r.json()["status"] == "blocked"
        assert r.json()["policy"]["reasons"][0].startswith("T0 tester scope")


SMUGGLED = [
    {"vlan": "999"}, {"vlan": 999.0}, {"vlan": True}, {"vlan": [999]}, {"vlan": 999 + 2 ** 64},
    {"ifname": "ge-0/0/5.0"}, {"ifname": "GE-0/0/5"}, {"ifname": " ge-0/0/5"}, {"ifname": "ge-0/0/5 "},
    {"ifname": "ge‑0/0/5"}, {"ifname": "ge-0/0/５"}, {"ifname": "ge-0/0/[5-6]"},
    {"ifname": "interface-range demo"}, {"ifname": "ge-0/0/*"}, {"device": "SW1"}, {"device": "sw1 "},
    {"approved": True}, {"apply-groups": "g"}, {"incident_id": 5}, {"incident_id": {"$ne": 1}},
]


@pytest.mark.parametrize("patch", SMUGGLED, ids=[json.dumps(p, ensure_ascii=True) for p in SMUGGLED])
def test_argument_smuggling_on_the_fixture_is_blocked(world, patch):
    t, h, _ = as_tester(world)
    r = t.post("/api/actions", json={**FIX, "args": {**fixture.ARGS, **patch}}, headers=h)
    assert r.status_code == 201 and r.json()["status"] == "blocked", r.text


@pytest.mark.parametrize("kind", ["Add_vlan_to_trunk", "add_vlan_to_trunk ", "add_vlan_to_trunk​"])
def test_kind_lookalikes_are_blocked(world, kind):
    t, h, _ = as_tester(world)
    assert t.post("/api/actions", json={**FIX, "kind": kind}, headers=h).json()["status"] == "blocked"


def test_mcp_tester_token_caller_cannot_be_widened_by_headers(world, monkeypatch):
    """Role and lease come only from the verified token: client labels and clientInfo grant nothing."""
    t, h, lease = as_tester(world)
    tok = t.post("/api/lease/token", headers=h).json()["token"]
    monkeypatch.setattr(mcp_mod, "_token_verifier", world.lm.verify_token)
    req = SimpleNamespace(headers={"authorization": f"Bearer {tok}", "x-shimpaku-client": "operator"},
                          client=SimpleNamespace(host="127.0.0.1"))
    meta = {"io.modelcontextprotocol/clientInfo": {"name": "dashboard"}}
    c = mcp_mod.caller_from_request(SimpleNamespace(meta=meta, request=req))
    assert (c.kind, c.role, c.lease_id, c.transport) == ("mcp", "tester", lease["id"], "http")
    assert world.eng.propose(**{**OTHER, "caller": c})["status"] == "blocked"
    a = world.eng.propose(**{**FIX, "caller": c})
    with pytest.raises(PermissionError):
        world.eng.approve(a["id"], c)


def test_an_http_mcp_request_never_inherits_an_inproc_caller(monkeypatch):
    """`transport=inproc` is honoured only when there is no HTTP request; a leaked context var
    cannot turn an unauthenticated HTTP call into Shim's caller."""
    token = mcp_mod._caller.set(Caller(kind="assistant", name="shim:admin", transport="inproc", role="agent"))
    try:
        monkeypatch.setattr(mcp_mod.settings, "auth_mode", "local")
        req = SimpleNamespace(headers={}, client=SimpleNamespace(host="10.0.0.9"))
        with pytest.raises(mcp_mod.Denied):
            mcp_mod.caller_from_request(SimpleNamespace(meta={}, request=req))
    finally:
        mcp_mod._caller.reset(token)


def _capture_shim(monkeypatch):
    from shimpaku.assistant import loop
    seen = {}

    class FakeAssistant:
        def __init__(self, target, llm_factory=None):
            pass

        async def ask(self, q):
            seen["caller"] = mcp_mod._caller.get(None)
            yield {"type": "done"}
    monkeypatch.setattr(service, "_mcp_server", object())
    monkeypatch.setattr(loop, "Assistant", FakeAssistant)
    return seen


@pytest.mark.parametrize("role", ["tester", "operator", "admin", "Tester", "system", None])
def test_shim_role_is_only_tester_or_agent_never_elevated(monkeypatch, role):
    seen = _capture_shim(monkeypatch)

    async def run():
        return [e async for e in service.assistant_ask("q", "p", role=role, lease_id="L")]
    asyncio.run(run())
    assert seen["caller"].role in ("tester", "agent", "visitor")
    assert seen["caller"].kind == "assistant"  # never a dashboard caller: Shim cannot approve
    assert seen["caller"].role == ("tester" if role == "tester" else "agent")


def test_testers_shim_is_held_to_the_tester_gate(world, monkeypatch):
    seen = _capture_shim(monkeypatch)

    async def run():
        return [e async for e in service.assistant_ask("q", "tester:inv_1", llm_key="sk-x", role="tester",
                                                       lease_id="lse_1")]
    asyncio.run(run())
    c = seen["caller"]
    assert world.eng.propose(**{**OTHER, "caller": c})["status"] == "blocked"
    op = world.eng.propose(**{**OTHER, "caller": Caller(kind="dashboard", name="admin")})
    with pytest.raises(PermissionError):
        world.eng.cancel(op["id"], c)
    with pytest.raises(PermissionError):
        world.eng.approve(op["id"], c)


def test_read_token_cannot_propose_through_shim(world, monkeypatch):
    """REGRESSION: a `read` bearer token could ask Shim, and Shim proposed as an `agent`
    (scope escalation read → propose through the in-process caller)."""
    seen = _capture_shim(monkeypatch)
    c, oh = operator(world)
    tok = c.post("/api/tokens", json={"name": "ro", "scope": "read"}, headers=oh).json()["token"]
    world.app.state.limiter.table["ask"] = (100, 100)
    r = client(world).post("/api/assistant/ask", json={"question": "bounce leaf2 ge-0/0/3"},
                           headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    shim = seen["caller"]
    out = world.eng.propose(**{**FIX, "caller": shim})
    assert out["status"] == "blocked", out
    # a propose-scope token's Shim still proposes (unchanged)
    tok2 = c.post("/api/tokens", json={"name": "rw", "scope": "propose"}, headers=oh).json()["token"]
    client(world).post("/api/assistant/ask", json={"question": "q"}, headers={"Authorization": f"Bearer {tok2}"})
    assert seen["caller"].role == "agent"


def test_cancel_and_repropose_is_bounded_by_the_write_limit(world):
    world.app.state.limiter.table["write"] = (6, 100)
    t, h, _ = as_tester(world)
    codes = []
    for _ in range(5):
        r = t.post("/api/actions", json=FIX, headers=h)
        codes.append(r.status_code)
        if r.status_code == 201 and r.json().get("id"):
            codes.append(t.post(f"/api/actions/{r.json()['id']}/cancel", headers=h).status_code)
    assert 429 in codes
    assert codes.count(201) <= 3


# ================================================================ session and lease

def test_session_is_dead_after_end_session(world):
    t, h, lease = as_tester(world)
    old_cookie = t.cookies.get("shimpaku_session")
    assert t.post("/api/lease/end", headers=h).status_code == 200
    replay = client(world)
    replay.cookies.set("shimpaku_session", old_cookie)
    replay.get("/api/whoami")
    for r in (replay.get("/api/actions"), replay.post("/api/actions", json=FIX, headers=csrf(replay)),
              replay.post("/api/demo/break", headers=csrf(replay))):
        assert r.status_code == 401
    assert replay.get("/api/whoami").json()["authenticated"] is False


def test_session_is_dead_after_operator_revoke_and_after_timeout(world):
    t, h, lease = as_tester(world)
    c, oh = operator(world)
    assert c.post(f"/api/admin/leases/{lease['id']}/revoke", headers=oh).status_code == 200
    assert t.get("/api/actions").status_code == 401
    t2, h2, _ = as_tester(world)
    world.clock.t += 1801  # lease over, even though the session's idle timer keeps sliding
    assert t2.get("/api/actions").status_code == 401
    assert t2.post("/api/actions", json=FIX, headers=h2).status_code == 401


def test_queued_tester_cannot_write(world, monkeypatch):
    as_tester(world)  # takes the only active slot
    q, qh, lease = as_tester(world)
    assert lease["state"] == "queued"
    for path, body in (("/api/actions", FIX), ("/api/demo/break", None), ("/api/demo/seed", None),
                       ("/api/lease/token", None), ("/api/assistant/ask", {"question": "q"})):
        r = q.post(path, json=body, headers=qh)
        assert r.status_code == 403, (path, r.status_code, r.text)
    a = other_lease_action(world, lease["id"])  # even an action under its lease id
    assert q.post(f"/api/actions/{a['id']}/approve", headers=qh).status_code == 403


def test_two_redemptions_racing_on_one_invite(world):
    tok = invite(world)
    results = []
    barrier = threading.Barrier(8)

    def go():
        barrier.wait()
        try:
            results.append(world.lm.redeem(tok, consent=True)["id"])
        except Exception as e:  # noqa: BLE001
            results.append(type(e).__name__)
    ts = [threading.Thread(target=go) for _ in range(8)]
    [x.start() for x in ts]
    [x.join() for x in ts]
    assert sum(1 for r in results if str(r).startswith("lse_")) == 1
    assert results.count("InviteError") == 7


def test_mcp_and_rest_token_dead_after_lease_end_and_across_leases(world):
    t, h, a = as_tester(world)
    tok_a = t.post("/api/lease/token", headers=h).json()["token"]
    assert t.post("/api/lease/token", headers=h).status_code == 409  # one per lease
    t.post("/api/lease/end", headers=h)
    b_client, bh, b = as_tester(world)
    assert b["state"] == "active"
    assert world.lm.verify_token(tok_a) is None
    r = client(world).post("/api/actions", json=FIX, headers={"Authorization": f"Bearer {tok_a}"})
    assert r.status_code == 401
    # B's own action cannot be touched with A's leftovers, and A's lease id is not B's
    act = b_client.post("/api/actions", json=FIX, headers=bh).json()
    assert act["caller"]["lease_id"] == b["id"] != a["id"]


def test_lease_token_expires_exactly_at_lease_end_even_if_not_revoked(world):
    t, h, lease = as_tester(world)
    tok = t.post("/api/lease/token", headers=h).json()["token"]
    world.clock.t = lease["ends"]
    assert world.auth.token_record(tok) is None


def test_invite_lookup_is_by_hash_and_wrong_tokens_never_consume(world):
    tok = invite(world)
    for guess in (tok[:-1], tok + "A", tok.upper(), "", " " + tok, tok[:20]):
        assert client(world).get("/api/invites/status", params={"i": guess}).json()["valid"] is False
    c = client(world)
    for _ in range(3):
        assert c.get(f"/start?i={tok}").status_code == 200
        assert c.get("/api/invites/status", params={"i": tok}).json()["valid"] is True
    assert c.head(f"/start?i={tok}").status_code in (200, 405)
    _, r = redeem(world, tok)
    assert r.status_code == 200
    stored = world.auth.db.execute("SELECT token_hash FROM invite").fetchall()
    assert (hashlib.sha256(tok.encode()).hexdigest(),) in stored


def test_invite_redeem_is_rate_limited_per_address(world):
    world.app.state.limiter.table["redeem"] = (5, 100)
    c = client(world)
    h = csrf(c)
    codes = [c.post("/api/invites/redeem", json={"token": f"guess{i}", "consent": True}, headers=h).status_code
             for i in range(7)]
    assert codes[:5] == [404] * 5 and codes[5:] == [429, 429]


TESTER_POSTS = ["/api/actions", "/api/actions/X/approve", "/api/actions/X/reject", "/api/actions/X/cancel",
                "/api/lease/end", "/api/lease/token", "/api/demo/break", "/api/demo/seed",
                "/api/assistant/ask", "/api/logout", "/api/invites/redeem"]


@pytest.mark.parametrize("path", TESTER_POSTS)
@pytest.mark.parametrize("attack", ["no_token", "wrong_token", "cross_origin", "cross_site"])
def test_csrf_on_every_tester_post(world, path, attack):
    t, h, lease = as_tester(world)
    good = dict(h)
    bad = {"no_token": {"Origin": good["Origin"]},
           "wrong_token": {**good, "X-CSRF-Token": "x" * 32},
           "cross_origin": {**good, "Origin": "https://evil.example"},
           "cross_site": {"X-CSRF-Token": good["X-CSRF-Token"], "Sec-Fetch-Site": "cross-site"}}[attack]
    body = {"question": "q"} if "ask" in path else ({"token": "x", "consent": True} if "redeem" in path else FIX)
    r = t.post(path, json=body, headers=bad)
    assert r.status_code == 403, (path, attack, r.status_code, r.text)
    assert world.lm.lease(lease["id"])["state"] == "active"


def test_host_prefixed_cookies_over_https(world):
    c = TestClient(world.app, base_url="https://testserver")
    c.get("/api/whoami")
    r = c.post("/api/invites/redeem", json={"token": invite(world), "consent": True},
               headers={"X-CSRF-Token": c.cookies.get("__Host-shimpaku_csrf"), "Origin": "https://testserver"})
    assert r.status_code == 200
    sets = r.headers.get_list("set-cookie")
    sess = [s for s in sets if s.startswith("__Host-shimpaku_session=")]
    assert sess, sets
    s = sess[0].lower()
    assert "secure" in s and "httponly" in s and "path=/" in s and "domain=" not in s and "samesite=lax" in s
    for s in sets:
        assert s.lower().startswith("__host-"), s  # nothing un-prefixed over https
    # the plain-http cookie name is not honoured over https
    c2 = TestClient(world.app, base_url="https://testserver")
    c2.cookies.set("shimpaku_session", c.cookies.get("__Host-shimpaku_session"))
    assert c2.get("/api/actions").status_code == 401


# ================================================================ abuse

def test_huge_bodies_are_refused_before_parsing(world):
    """REGRESSION: a tester could post megabytes of `args`; every key was stored on the action
    row and in the audit chain (the audit redactor caps lists and strings, not dict keys)."""
    t, h, _ = as_tester(world)
    big = {f"k{i}": i for i in range(200_000)}
    r = t.post("/api/actions", json={**FIX, "args": {**fixture.ARGS, **big}}, headers=h)
    assert r.status_code == 413, r.status_code
    r = t.post("/api/assistant/ask", json={"question": "q", "context": big}, headers=h)
    assert r.status_code == 413
    n = world.eng.store.list()
    assert all(len(json.dumps(a["args"])) < 10_000 for a in n)


def test_rate_limits_on_propose_break_seed(world, monkeypatch):
    async def ok(caller):
        return {"op": "x", "changed": False, "writes": []}
    monkeypatch.setattr(service, "demo_break", ok)
    monkeypatch.setattr(service, "demo_seed", ok)
    world.app.state.limiter.table.update({"write": (3, 100), "demo": (2, 100)})
    t, h, _ = as_tester(world)
    assert [t.post("/api/actions", json=OTHER, headers=h).status_code for _ in range(4)][-1] == 429
    codes = [t.post("/api/demo/break", headers=h).status_code, t.post("/api/demo/seed", headers=h).status_code,
             t.post("/api/demo/break", headers=h).status_code]
    assert codes == [200, 200, 429]


def test_five_break_cap_survives_a_new_session_on_the_same_lease(world, monkeypatch):
    async def ok(caller):
        return {"op": "x", "changed": False, "writes": []}
    monkeypatch.setattr(service, "demo_break", ok)
    t, h, lease = as_tester(world)
    for _ in range(5):
        assert t.post("/api/demo/break", headers=h).status_code == 200
    other = client(world)
    other.cookies.set("shimpaku_session", world.auth.create_session(
        _tname(lease["invite_id"]), role="tester", lease_id=lease["id"]))
    assert other.post("/api/demo/break", headers=csrf(other)).status_code == 429


def test_lease_cannot_be_extended(world):
    t, h, lease = as_tester(world)
    ends = lease["ends"]
    for _ in range(3):
        world.clock.t += 500
        t.get("/api/actions")
        t.post("/api/lease/token", headers=h)
    assert world.lm.lease(lease["id"])["ends"] == ends
    # re-redeeming the same invite after the end does not start a new lease
    world.clock.t = ends + 1
    asyncio.run(world.lm.tick())
    assert t.get("/api/lease").status_code == 401


@respx.mock
def test_ask_is_rate_limited_for_a_tester(world):
    respx.post(f"{GW}/key/generate").mock(return_value=httpx.Response(200, json={"key": "sk-lease"}))

    async def ask_fn(question, principal, context, llm_key=None, role=None, lease_id=None):
        yield {"type": "done"}
    lm = LeaseManager(world.auth, world.seeder, world.keys, clock=world.clock, action_store=lambda: world.eng.store)
    app = create_app(service, auth_mode="local", leases=lm, assistant=ask_fn,
                     limiter=RateLimiter({"ask": (2, 100)}))
    inv = lm.create_invite("n", "admin")
    lease = lm.redeem(inv["token"], consent=True)
    c = TestClient(app)
    c.cookies.set("shimpaku_session", world.auth.create_session(_tname(inv["id"]), role="tester",
                                                                 lease_id=lease["id"]))
    codes = [c.post("/api/assistant/ask", json={"question": "q"}, headers=csrf(c)).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


OPERATOR_ONLY = [("GET", "/api/tokens"), ("POST", "/api/tokens"), ("DELETE", "/api/tokens/tok_x"),
                 ("GET", "/api/admin/invites"), ("POST", "/api/admin/invites"),
                 ("DELETE", "/api/admin/invites/inv_x"), ("GET", "/api/admin/leases"),
                 ("POST", "/api/admin/leases/lse_x/revoke"), ("POST", "/api/admin/purge")]


@pytest.mark.parametrize("method,path", OPERATOR_ONLY)
def test_operator_only_routes_refuse_tester_token_and_anonymous(world, method, path):
    t, h, _ = as_tester(world)
    tok = t.post("/api/lease/token", headers=h).json()["token"]
    body = {"name": "x", "scope": "propose"} if path == "/api/tokens" else {"note": "x"}
    kw = {"json": body} if method == "POST" else {}
    assert t.request(method, path, headers=h, **kw).status_code == 403
    assert client(world).request(method, path, headers={"Authorization": f"Bearer {tok}"}, **kw).status_code == 403
    anon = client(world)
    assert anon.request(method, path, headers=csrf(anon), **kw).status_code == 401
    # and nothing about settings is writable by anyone
    for m in ("POST", "PUT", "PATCH", "DELETE"):
        assert t.request(m, "/api/settings", headers=h).status_code in (404, 405)


def test_anonymous_can_read_nothing_but_the_public_endpoints(world):
    anon = client(world)
    for path in ("/api/actions", "/api/audit", "/api/settings", "/api/lease", "/api/inventory",
                 "/api/stream/actions", "/api/audit/verify"):
        assert anon.get(path).status_code == 401, path
    w = anon.get("/api/whoami").json()
    assert w["authenticated"] is False and w["role"] is None


# ================================================================ data exposure

def _secrets(world, extra=()):
    return [NOTE, NAME, "sk-admin-MASTER", PW, *extra]


def test_nothing_a_tester_reads_leaks_identity_or_secrets(world, monkeypatch):
    from shimpaku.config import settings
    monkeypatch.setattr(settings, "device_password", "DEVPASS-xyz")
    monkeypatch.setattr(settings, "llm_key", "sk-OPERATOR-key")
    c, oh = operator(world)
    op_tok = c.post("/api/tokens", json={"name": "laptop", "scope": "propose"}, headers=oh).json()["token"]
    # a first tester with a note + display name, then a second tester who looks around
    t1, h1, l1 = as_tester(world)
    t1_tok = t1.post("/api/lease/token", headers=h1).json()["token"]
    t1.post("/api/actions", json=FIX, headers=h1)
    t1.post("/api/lease/end", headers=h1)
    asyncio.run(world.lm.tick())
    t2, h2, l2 = as_tester(world)
    t2.post("/api/actions", json={**FIX, "args": {**fixture.ARGS, "password": "hunter2"}}, headers=h2)
    secrets = _secrets(world, ("DEVPASS-xyz", "sk-OPERATOR-key", op_tok, t1_tok))
    for path in ("/api/settings", "/api/audit?limit=1000", "/api/lease", "/api/actions", "/api/whoami",
                 "/api/audit/verify"):
        r = t2.get(path)
        assert r.status_code == 200, path
        for s in secrets:
            assert s not in r.text, (path, s)
    assert "invite_id" not in json.dumps(t2.get("/api/lease").json().get("leases") or [])
    # the audit chain itself never held them
    rows = json.dumps(world.audit.query(limit=10_000))
    for s in (*secrets, "hunter2"):  # a secret-looking argument is redacted in the chain
        assert s not in rows, s


def test_device_errors_do_not_leak_management_addresses_to_a_tester(world, monkeypatch):
    """REGRESSION: a demo operation that failed to reach the device returned the exception text,
    management address included, to the tester and into the audit rows every reader sees."""
    from shimpaku import devices

    def boom(name, **kw):
        raise ConnectionRefusedError(f"ConnectRefusedError(192.168.122.11): ssh to 192.168.122.11:830 "
                                     f"refused; host fd00:10::11")
    monkeypatch.setattr(devices, "open_device", boom)
    t, h, _ = as_tester(world)
    r = t.post("/api/demo/break", headers=h)
    assert r.status_code == 502
    for leak in ("192.168.122.11", "fd00:10::11"):
        assert leak not in r.text
        assert leak not in t.get("/api/audit?limit=1000").text


def test_mcp_inventory_hides_management_addresses_from_a_tester(world):
    """REGRESSION: a tester's agent (token or Shim) listed every device's management IP."""
    from mcp.client import Client
    from shimpaku.mcp.audit import AuditLog
    from shimpaku import devices

    inv = devices.Inventory()
    inv.nodes["sw1"] = devices.Node("sw1", "campus", "vjunos-switch", "192.168.122.31", "campus")

    srv = mcp_mod.build_server(AuditLog(world.tmp / "mcp.db", world.tmp / "mcp.jsonl"))

    async def run(caller):
        token = mcp_mod._caller.set(caller)
        try:
            async with Client(srv) as c:
                return (await c.call_tool("inventory_list", {})).structured_content
        finally:
            mcp_mod._caller.reset(token)

    import shimpaku.devices as d
    orig = d._inventory
    d._inventory = inv
    try:
        tester_view = asyncio.run(run(Caller(kind="assistant", name="shim:t", transport="inproc", role="tester",
                                             lease_id="L")))
        agent_view = asyncio.run(run(Caller(kind="assistant", name="shim:admin", transport="inproc", role="agent")))
    finally:
        d._inventory = orig
    assert "192.168.122.31" not in json.dumps(tester_view)
    assert tester_view["nodes"][0]["name"] == "sw1"
    assert "192.168.122.31" in json.dumps(agent_view)  # the operator's agents keep it


def test_errors_are_never_tracebacks(world, monkeypatch):
    async def explode(*a, **k):
        raise RuntimeError("internal detail /home/lab/secret-path DEVPASS")
    monkeypatch.setattr(service, "action_list", explode)
    t, h, _ = as_tester(world)
    c = TestClient(world.app, raise_server_exceptions=False)
    c.cookies = t.cookies
    r = c.get("/api/actions")
    assert r.status_code == 500
    assert "Traceback" not in r.text and "secret-path" not in r.text and "DEVPASS" not in r.text


# ================================================================ auth_mode=off

def test_auth_off_anonymous_is_the_operator(world):
    app = create_app(service, auth_mode="off", leases=world.lm, limiter=RateLimiter({}))
    c = TestClient(app)
    w = c.get("/api/whoami").json()
    assert w["role"] == "operator" and w["can_approve"] is True
    assert c.get("/api/tokens").status_code == 200  # expected: off = no sign-in, loopback only


def test_auth_off_rest_refuses_a_non_loopback_bind(monkeypatch):
    """REGRESSION: `python -m shimpaku.api` with SHIMPAKU_AUTH_MODE=off served every caller as the
    operator on whatever SHIMPAKU_BIND said, 0.0.0.0 included (only the MCP endpoint refused)."""
    from shimpaku.api import __main__ as api_main
    from shimpaku.config import settings
    started = []
    monkeypatch.setattr(api_main.uvicorn, "run", lambda *a, **k: started.append(k.get("host")))
    monkeypatch.setenv("SHIMPAKU_DEMO", "1")
    monkeypatch.delenv("SHIMPAKU_REPLAY", raising=False)
    monkeypatch.setattr(settings, "auth_mode", "off")
    for bind in ("0.0.0.0", "::", "192.168.1.5"):
        monkeypatch.setattr(settings, "bind", bind)
        with pytest.raises(RuntimeError, match="loopback"):
            api_main.main()
    assert started == []
    monkeypatch.setattr(settings, "bind", "127.0.0.1")
    api_main.main()
    assert started == ["127.0.0.1"]
    monkeypatch.setattr(settings, "auth_mode", "local")
    monkeypatch.setattr(settings, "bind", "0.0.0.0")
    api_main.main()  # local mode may bind wide (behind the edge)
    assert started[-1] == "0.0.0.0"


def test_auth_off_mcp_refuses_a_non_loopback_bind(monkeypatch):
    monkeypatch.setattr(mcp_mod.settings, "auth_mode", "off")
    monkeypatch.setattr(mcp_mod.settings, "bind", "0.0.0.0")
    with pytest.raises(RuntimeError, match="non-loopback"):
        mcp_mod.http_app(object())
