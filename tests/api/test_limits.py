"""Build step 5 (docs/DEPLOY-DESIGN.md): rate limits, the per-lease LiteLLM key, and HTTP 401 on
/mcp. The gateway is mocked with respx; the MCP app behind the gate is a recorder."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "actions"))
from test_engine import box  # noqa: E402,F401
from test_tester_passes import GW, csrf, invite, operator, redeem, world  # noqa: E402,F401

from shimpaku.actions import fixture  # noqa: E402
from shimpaku.api import demo  # noqa: E402
from shimpaku.api.app import create_app  # noqa: E402
from shimpaku.api.auth import AuthStore  # noqa: E402
from shimpaku.api.lease import LeaseManager  # noqa: E402
from shimpaku.api.llm_keys import LiteLLMKeys  # noqa: E402
from shimpaku.api.ratelimit import RateLimiter  # noqa: E402
from shimpaku.mcp import server  # noqa: E402
from shimpaku.mcp.server import McpHttpGate  # noqa: E402


# ---- rate limiter --------------------------------------------------------------------------------

def test_token_bucket_per_principal_and_global():
    t = [0.0]
    rl = RateLimiter({"write": (2, 3)}, clock=lambda: t[0])
    assert rl.check("write", "a") is None and rl.check("write", "a") is None
    assert rl.check("write", "a") == 30  # 2/min: one token every 30 s
    assert rl.check("write", "b") is None
    assert rl.check("write", "c") is not None  # global 3/min spent (a, a, b)
    t[0] += 30
    assert rl.check("write", "c") is None
    assert rl.check("other", "a") is None  # no table entry: unlimited


def test_rest_writes_are_rate_limited_with_retry_after(world):
    world.app.state.limiter.table["write"] = (2, 100)
    c, h = operator(world)
    for _ in range(2):
        assert c.post("/api/actions/x/cancel", headers=h).status_code in (404, 409)
    r = c.post("/api/actions/x/cancel", headers=h)
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1


def test_demo_ops_and_redeem_are_rate_limited(world, monkeypatch):
    from shimpaku import service
    world.app.state.limiter.table.update({"demo": (1, 100), "redeem": (2, 100)})

    async def ok(caller):
        return {"op": "x", "changed": False, "writes": []}
    monkeypatch.setattr(service, "demo_seed", ok)
    c, h = operator(world)
    assert c.post("/api/demo/seed", headers=h).status_code == 200
    assert c.post("/api/demo/seed", headers=h).status_code == 429
    toks = [invite(world) for _ in range(3)]
    codes = [redeem(world, t)[1].status_code for t in toks]
    assert codes == [200, 200, 429]


def test_operator_flow_unchanged_under_default_limits(monkeypatch):
    """The defaults leave a normal operator session alone (demo service, auth off)."""
    demo._approved.clear()
    c = TestClient(create_app(demo, auth_mode="off"))
    c.get("/api/whoami")
    h = {"X-CSRF-Token": c.cookies.get("shimpaku_csrf"), "Origin": "http://testserver"}
    for i in range(10):
        assert c.post(f"/api/actions/{40 + i}/approve", headers=h).status_code != 429
        assert c.post("/api/actions", json={"kind": "bounce_port", "args": {}}, headers=h).status_code != 429
    assert c.get("/api/whoami").json()["role"] == "operator"


# ---- per-lease LiteLLM key -----------------------------------------------------------------------

def _ask_app(world, keys):
    seen = {}

    async def ask_fn(question, principal, context, llm_key=None):
        seen["key"] = llm_key
        yield {"type": "done"}
    lm = LeaseManager(world.auth, world.seeder, keys, clock=world.clock, action_store=lambda: world.eng.store)
    from shimpaku import service
    app = create_app(service, auth_mode="local", leases=lm, assistant=ask_fn,
                     limiter=RateLimiter({"ask": (100, 100)}))
    return app, lm, seen


@respx.mock
def test_tester_ask_uses_the_lease_key_never_the_operators(world, monkeypatch, caplog):
    from shimpaku.config import settings
    monkeypatch.setattr(settings, "llm_key", "sk-OPERATOR")
    gen = respx.post(f"{GW}/key/generate").mock(return_value=httpx.Response(200, json={"key": "sk-lease-abc"}))
    keys = LiteLLMKeys(GW, "sk-admin-MASTER", budget_usd=0.5, rpm=7, models=["m1"])
    app, lm, seen = _ask_app(world, keys)
    inv = lm.create_invite("n", "admin")
    lease = lm.redeem(inv["token"], consent=True)
    c = TestClient(app)
    c.get("/api/whoami")
    from shimpaku.api.lease import tester_name
    sid = world.auth.create_session(tester_name(inv["id"]), role="tester", lease_id=lease["id"])
    c.cookies.set("shimpaku_session", sid)
    caplog.set_level(logging.DEBUG)
    r = c.post("/api/assistant/ask", json={"question": "why?"}, headers=csrf(c))
    assert r.status_code == 200, r.text
    assert seen["key"] == "sk-lease-abc"
    body = json.loads(gen.calls[0].request.content)
    assert body == {"key_alias": f"shimpaku-lease-{lease['id']}", "max_budget": 0.5, "duration": "1800s",
                    "rpm_limit": 7, "models": ["m1"], "metadata": {"shimpaku_lease": lease["id"]}}
    assert "sk-OPERATOR" not in str(gen.calls[0].request.headers)
    assert "sk-lease-abc" not in caplog.text and "sk-admin-MASTER" not in caplog.text
    assert "sk-" not in repr(keys)
    # a second ask reuses the key; lease end deletes it
    c.post("/api/assistant/ask", json={"question": "again"}, headers=csrf(c))
    assert gen.call_count == 1
    dele = respx.post(f"{GW}/key/delete").mock(return_value=httpx.Response(200, json={}))
    asyncio.run(lm.end(lease["id"], "tester"))
    assert json.loads(dele.calls[0].request.content) == {"keys": ["sk-lease-abc"]}


def test_without_an_admin_key_testers_get_no_assistant(world, monkeypatch):
    from shimpaku.config import settings
    monkeypatch.setattr(settings, "llm_key", "sk-OPERATOR")
    app, lm, seen = _ask_app(world, LiteLLMKeys(GW, None, budget_usd=0.5, rpm=7, models=[]))
    inv = lm.create_invite("n", "admin")
    lease = lm.redeem(inv["token"], consent=True)
    c = TestClient(app)
    c.get("/api/whoami")
    from shimpaku.api.lease import tester_name
    c.cookies.set("shimpaku_session", world.auth.create_session(tester_name(inv["id"]), role="tester",
                                                                 lease_id=lease["id"]))
    with respx.mock(assert_all_called=False) as mock:
        r = c.post("/api/assistant/ask", json={"question": "why?"}, headers=csrf(c))
        assert not mock.calls
    assert r.status_code == 503 and "not enabled for testers" in r.json()["detail"]
    assert "key" not in seen


@respx.mock
def test_key_is_deleted_by_alias_after_a_restart(world):
    respx.post(f"{GW}/key/generate").mock(return_value=httpx.Response(200, json={"key": "sk-1"}))
    dele = respx.post(f"{GW}/key/delete").mock(return_value=httpx.Response(200, json={}))
    keys = LiteLLMKeys(GW, "sk-admin", budget_usd=0.5, rpm=7, models=[])
    lm = LeaseManager(world.auth, world.seeder, keys, clock=world.clock)
    lease = lm.redeem(lm.create_invite("n", "admin")["token"], consent=True)
    asyncio.run(lm.llm_key(lease["id"]))
    lm2 = LeaseManager(AuthStore(world.tmp / "auth.db"), world.seeder, keys, clock=world.clock)
    asyncio.run(lm2.end(lease["id"], "operator"))
    assert json.loads(dele.calls[0].request.content) == {"key_aliases": [f"shimpaku-lease-{lease['id']}"]}


@respx.mock
def test_gateway_failure_never_leaks_the_admin_key():
    respx.post(f"{GW}/key/generate").mock(return_value=httpx.Response(500, text="sk-admin-SECRET bad"))
    keys = LiteLLMKeys(GW, "sk-admin-SECRET", budget_usd=0.5, rpm=7, models=[])
    with pytest.raises(Exception) as ei:
        asyncio.run(keys.generate("L1", 1800))
    assert "sk-admin" not in str(ei.value)


# ---- /mcp: HTTP 401 / 403 / 429 ------------------------------------------------------------------

class Inner:
    def __init__(self):
        self.bodies: list[bytes] = []

    async def __call__(self, scope, receive, send):
        msg = await receive()
        self.bodies.append(msg.get("body", b""))
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b"{}"})


def _mcp(gate, token=None, method="tools/call", tool="inventory_list", host="10.0.0.9"):
    async def go():
        t = httpx.ASGITransport(app=gate, client=(host, 1234))
        async with httpx.AsyncClient(transport=t, base_url="http://x") as c:
            h = {"Authorization": f"Bearer {token}"} if token else {}
            return await c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": method,
                                                          "params": {"name": tool, "arguments": {}}})
    return asyncio.run(go())


@pytest.fixture
def gate(world):
    inner = Inner()
    limiter = RateLimiter({"mcp": (3, 100)})
    return SimpleNamespace(gate=McpHttpGate(inner, world.lm.verify_token, limiter, auth_mode="local"), inner=inner,
                           lm=world.lm, auth=world.auth, clock=world.clock, limiter=limiter)


def test_mcp_missing_token_is_http_401_with_www_authenticate(gate):
    r = _mcp(gate.gate)
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    assert gate.inner.bodies == []


def test_mcp_invalid_expired_revoked_tokens_are_401(gate):
    assert _mcp(gate.gate, "shp_nope").status_code == 401
    tok = gate.auth.create_token("a", "read", "admin", expires_at=gate.clock() + 10)
    assert _mcp(gate.gate, tok["token"]).status_code == 200
    gate.clock.t += 11
    r = _mcp(gate.gate, tok["token"])
    assert r.status_code == 401 and 'error="invalid_token"' in r.headers["www-authenticate"]
    tok2 = gate.auth.create_token("b", "read", "admin")  # operator token: NULL expiry, never expires
    gate.clock.t += 10 ** 7
    assert _mcp(gate.gate, tok2["token"]).status_code == 200
    gate.auth.revoke_token(tok2["id"])
    assert _mcp(gate.gate, tok2["token"]).status_code == 401


def test_mcp_body_reaches_the_app_intact(gate):
    tok = gate.auth.create_token("a", "read", "admin")
    assert _mcp(gate.gate, tok["token"]).status_code == 200
    assert json.loads(gate.inner.bodies[0])["params"]["name"] == "inventory_list"


def test_mcp_read_token_cannot_call_a_propose_tool(gate):
    tok = gate.auth.create_token("a", "read", "admin")
    r = _mcp(gate.gate, tok["token"], tool="action_propose_bounce_port")
    assert r.status_code == 403 and "insufficient_scope" in r.headers["www-authenticate"]


def test_mcp_tool_calls_are_rate_limited(gate):
    tok = gate.auth.create_token("a", "propose", "admin")
    codes = [_mcp(gate.gate, tok["token"]).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    r = _mcp(gate.gate, tok["token"])
    assert int(r.headers["retry-after"]) >= 1
    assert _mcp(gate.gate, tok["token"], method="tools/list").status_code == 200  # only tools/call counts


def test_mcp_auth_off_still_allows_loopback_without_a_token(world):
    g = McpHttpGate(Inner(), world.lm.verify_token, None, auth_mode="off")
    assert _mcp(g, host="127.0.0.1").status_code == 200
    assert _mcp(g, host="10.0.0.9").status_code == 401


def test_tester_token_on_mcp_only_within_lease_and_only_the_fixture(gate, world, monkeypatch):
    """The gate admits the tester's token while the lease is active; the verified record gives the
    MCP caller role tester + the lease, so the engine allows the fixture and nothing else."""
    lease = gate.lm.redeem(gate.lm.create_invite("n", "admin")["token"], consent=True)
    tok = gate.lm.issue_token(lease["id"])["token"]
    assert _mcp(gate.gate, tok, tool="action_propose_add_vlan_to_trunk").status_code == 200
    monkeypatch.setattr(server, "_token_verifier", gate.lm.verify_token)
    req = SimpleNamespace(headers={"authorization": f"Bearer {tok}"}, client=SimpleNamespace(host="10.0.0.9"))
    caller = server.caller_from_request(SimpleNamespace(meta={}, request=req))
    assert (caller.role, caller.lease_id) == ("tester", lease["id"])
    ok = world.eng.propose(fixture.KIND, dict(fixture.ARGS), "fix", caller)
    assert ok["status"] == "awaiting_approval"
    bad = world.eng.propose("bounce_port", {"device": "leaf2", "ifname": "ge-0/0/3"}, "x", caller)
    assert bad["status"] == "blocked" and bad["policy"]["reasons"][0].startswith("T0 tester scope")
    asyncio.run(gate.lm.end(lease["id"], "tester"))
    assert _mcp(gate.gate, tok).status_code == 401
    with pytest.raises(server.Denied):
        server.caller_from_request(SimpleNamespace(meta={}, request=req))
