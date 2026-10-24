"""Build step 2 (docs/DEPLOY-DESIGN.md): the tester gate, enforced in the engine so REST and MCP
both get it; and device-less actions (rerun_deploy) no longer skip G0/G5."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_engine import box, make_engine  # noqa: E402,F401

from shimpaku.actions import fixture  # noqa: E402
from shimpaku.actions import policy as P  # noqa: E402
from shimpaku.actions.engine import ApprovalRefused, Caller  # noqa: E402

OPERATOR = Caller(kind="dashboard", name="admin", role="operator")
AGENT = Caller(kind="mcp", name="claude-code", transport="stdio")


def _tester(lease="L1", kind="dashboard"):
    return Caller(kind=kind, name=f"tester:{lease}", role="tester", lease_id=lease,
                  transport="rest" if kind == "dashboard" else "http")


@pytest.fixture
def eng(tmp_path, box):  # noqa: F811
    e = make_engine(tmp_path, box)
    calls = {"n": 0}

    def facts_fn(kind, args):
        calls["n"] += 1
        return P.Facts(known_devices={"sw1", "leaf2", "spine2"}, topology_role="unused", pending_confirm=False,
                       vlan_seen_elsewhere=True,
                       port={"live_role": "trunk" if kind == "add_vlan_to_trunk" else "unused",
                             "interface_range_lines": [], "lldp_neighbor": None,
                             "config_own": "", "config_inherited": ""},
                       open_actions_device=e.store.count_open(args.get("device")),
                       open_actions_total=e.store.count_open(),
                       incident={"id": "inc-1", "kind": "drift"} if args.get("incident_id") == "inc-1" else None)
    e.facts_fn = facts_fn
    e.facts_calls = calls
    return e


NOT_FIXTURE = [
    ("bounce_port", {"device": "leaf2", "ifname": "ge-0/0/3"}),
    ("disable_port", {"device": "leaf2", "ifname": "ge-0/0/3"}),
    ("rollback", {"device": "sw1", "rollback_id": 1}),
    ("rerun_deploy", {"topology": "campus.toml"}),
    ("bounce_port", {"device": "sw1", "ifname": "ge-0/0/5"}),
    ("add_vlan_to_trunk", {"device": "sw1", "ifname": "ge-0/0/5", "vlan": 998}),
    ("add_vlan_to_trunk", {"device": "sw1", "ifname": "ge-0/0/6", "vlan": 999}),
    ("add_vlan_to_trunk", {"device": "leaf2", "ifname": "ge-0/0/5", "vlan": 999}),
    ("add_vlan_to_trunk", {"device": "sw1", "ifname": "ge-0/0/5", "vlan": "999"}),
    ("add_vlan_to_trunk", {"device": "sw1", "ifname": "ge-0/0/5", "vlan": 999.0}),
    ("add_vlan_to_trunk", {"device": "sw1", "ifname": "ge-0/0/5", "vlan": 999, "approved": True}),
]


@pytest.mark.parametrize("kind,args", NOT_FIXTURE, ids=[f"{k}-{a}" for k, a in NOT_FIXTURE])
def test_tester_cannot_propose_anything_but_the_fixture(eng, kind, args):
    a = eng.propose(kind, args, "demo", _tester())
    assert a["status"] == "blocked"
    assert a["policy"]["reasons"][0].startswith("T0 tester scope"), a["policy"]["reasons"]
    assert eng.facts_calls["n"] == 0  # refused on role alone: no device was opened


def test_tester_without_lease_is_refused_even_for_the_fixture(eng):
    a = eng.propose(fixture.KIND, dict(fixture.ARGS), "demo", Caller(kind="dashboard", name="t", role="tester"))
    assert a["status"] == "blocked" and "no lease" in a["policy"]["reasons"][0]


def test_tester_may_propose_the_fixture(eng):
    a = eng.propose(fixture.KIND, {**fixture.ARGS, "incident_id": "nope"}, "restore", _tester())
    assert a["status"] == "blocked" and a["policy"]["reasons"][0].startswith("G6")  # policy still runs
    a = eng.propose(fixture.KIND, dict(fixture.ARGS), "restore", _tester())
    assert a["status"] == "awaiting_approval", a["policy"]["reasons"]
    assert a["caller"]["role"] == "tester" and a["caller"]["lease_id"] == "L1"


def test_tester_policy_overlay_narrows_g0_even_without_the_gate(eng):
    """Defence in depth: under the tester overlay G0 alone refuses a leaf2 test port."""
    pol = fixture.tester_policy(eng.policy)
    f = eng.facts_fn("bounce_port", {"device": "leaf2"})
    d = P.evaluate("bounce_port", {"device": "leaf2", "ifname": "ge-0/0/3"}, "x", f, pol)
    assert d.status == "blocked" and any("outside the write scope" in r for r in d.reasons)


def test_tester_cannot_approve_reject_or_cancel_an_operators_action(eng):
    a = eng.propose(fixture.KIND, dict(fixture.ARGS), "restore", OPERATOR)
    assert a["status"] == "awaiting_approval"
    for verb in (eng.approve, eng.reject, eng.cancel):
        with pytest.raises(ApprovalRefused, match="lease"):
            verb(a["id"], _tester())
    assert eng.get(a["id"])["status"] == "awaiting_approval"
    assert [r["decision"] for r in eng.store.approvals(a["id"])] == ["refused"]


def test_tester_cannot_act_on_another_leases_action(eng):
    a = eng.propose(fixture.KIND, dict(fixture.ARGS), "restore", _tester("L1"))
    for verb in (eng.approve, eng.reject, eng.cancel):
        with pytest.raises(ApprovalRefused, match="lease"):
            verb(a["id"], _tester("L2"))
    assert eng.get(a["id"])["status"] == "awaiting_approval"


def test_tester_cannot_act_on_a_non_fixture_action_even_in_their_lease(eng):
    """A row marked with the tester's lease but not the fixture (e.g. written before the gate)."""
    a = eng.propose("bounce_port", {"device": "spine2", "ifname": "ge-0/0/9"}, "x", AGENT)
    row = eng.store.db.action_get(a["id"])
    import json
    eng.store.db.action_update(a["id"], caller=json.dumps({**json.loads(row["caller"]), "lease_id": "L1"}))
    with pytest.raises(ApprovalRefused, match="fixture"):
        eng.approve(a["id"], _tester("L1"))


def test_tester_approves_and_cancels_own_lease_fixture(eng):
    a = eng.propose(fixture.KIND, dict(fixture.ARGS), "restore", _tester("L1", kind="mcp"))  # their own agent
    assert eng.approve(a["id"], _tester("L1"))["status"] == "approved"
    assert eng.store.approvals(a["id"])[-1]["note"].endswith("dashboard:tester")
    eng.store.transition(a["id"], "awaiting_approval", "reset for test")
    assert eng.cancel(a["id"], _tester("L1"))["status"] == "cancelled"


def test_tester_token_never_approves(eng):
    a = eng.propose(fixture.KIND, dict(fixture.ARGS), "restore", _tester("L1"))
    with pytest.raises(ApprovalRefused, match="not a dashboard session"):
        eng.approve(a["id"], _tester("L1", kind="mcp"))


def test_operator_unchanged(eng):
    a = eng.propose("bounce_port", {"device": "leaf2", "ifname": "ge-0/0/3"}, "lab test", OPERATOR)
    assert a["status"] == "awaiting_approval"
    assert eng.approve(a["id"], OPERATOR)["status"] == "approved"
    assert eng.store.approvals(a["id"])[-1]["note"] == "via dashboard"
    b = eng.propose(fixture.KIND, dict(fixture.ARGS), "restore", _tester("L1"))
    assert eng.reject(b["id"], OPERATOR)["status"] == "rejected"  # operators act on any action
    # a role-less dashboard dict (how callers looked before roles) is still the operator
    c = eng.propose("bounce_port", {"device": "spine2", "ifname": "ge-0/0/9"}, "x", AGENT)
    assert c["status"] == "awaiting_approval", c["policy"]["reasons"]
    assert eng.cancel(c["id"], {"kind": "dashboard", "name": "admin"})["status"] == "cancelled"


def test_visitor_can_do_nothing(eng):
    v = Caller(kind="dashboard", name="x", role="visitor")
    a = eng.propose("bounce_port", {"device": "leaf2", "ifname": "ge-0/0/3"}, "x", v)
    assert a["status"] == "blocked"
    b = eng.propose("bounce_port", {"device": "leaf2", "ifname": "ge-0/0/3"}, "x", OPERATOR)
    for verb in (eng.approve, eng.reject, eng.cancel):
        with pytest.raises(ApprovalRefused):
            verb(b["id"], v)


# ---- device-less actions (rerun_deploy) no longer skip G0 / G5, for everyone ---------------------

def test_rerun_deploy_fails_closed_on_g0(eng):
    a = eng.propose("rerun_deploy", {"topology": "campus.toml"}, "drift", OPERATOR)
    assert a["status"] == "blocked"
    assert any(r.startswith("G0") and "write_scope_topologies" in r for r in a["policy"]["reasons"])


def test_rerun_deploy_scoped_and_g5_lab_wide(eng):
    eng.policy["write_scope_topologies"] = ["campus.toml"]
    a = eng.propose("rerun_deploy", {"topology": "campus.toml", "incident_id": "inc-1"}, "drift", OPERATOR)
    assert a["status"] == "awaiting_approval", a["policy"]["reasons"]
    assert "G5" in a["policy"]["checked"]
    eng.cancel(a["id"], OPERATOR)
    eng.propose("bounce_port", {"device": "leaf2", "ifname": "ge-0/0/3"}, "x", OPERATOR)  # in flight
    b = eng.propose("rerun_deploy", {"topology": "campus.toml", "incident_id": "inc-1"}, "drift", OPERATOR)
    assert b["status"] == "blocked" and any(r.startswith("G5") for r in b["policy"]["reasons"])


# ---- MCP: the tool path reaches the same gate ----------------------------------------------------

def test_mcp_bearer_token_role_comes_from_the_verified_token(monkeypatch):
    from shimpaku.mcp import server
    monkeypatch.setattr(server, "_token_verifier",
                        lambda v: {"id": "tok_1", "client_name": "tester-agent", "role": "tester", "lease_id": "L9"}
                        if v == "good" else None)
    req = SimpleNamespace(headers={"authorization": "Bearer good"}, client=SimpleNamespace(host="10.0.0.5"))
    c = server.caller_from_request(SimpleNamespace(meta={}, request=req))
    assert (c.kind, c.role, c.lease_id) == ("mcp", "tester", "L9")


# ---- REST: POST /api/actions reaches the same gate --------------------------------------------

@pytest.mark.parametrize("kind,args", NOT_FIXTURE[:4] + NOT_FIXTURE[5:8],
                         ids=[f"{k}-{a}" for k, a in NOT_FIXTURE[:4] + NOT_FIXTURE[5:8]])
def test_tester_blocked_outside_fixture_over_rest(eng, monkeypatch, tmp_path, kind, args):
    from fastapi.testclient import TestClient

    from shimpaku import service
    from shimpaku.actions import engine as engmod
    from shimpaku.api.app import create_app
    from shimpaku.api.auth import Principal
    from shimpaku.mcp.audit import AuditLog

    engmod.set_engine(eng)
    audit = AuditLog(tmp_path / "audit.db", tmp_path / "audit.jsonl")
    service.set_audit_log(audit)
    try:
        app = create_app(service, auth_mode="local")
        # no tester tokens exist until build step 4; stand one in for the token table
        monkeypatch.setattr(app.state.auth, "token_principal", lambda v: Principal(
            "token", "token:tester", "propose", token_id="tok_t", role="tester", lease_id="L1"))
        r = TestClient(app).post("/api/actions", json={"kind": kind, "args": args, "reason": "demo"},
                                 headers={"Authorization": "Bearer x"})
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "blocked" and r.json()["policy"]["reasons"][0].startswith("T0 tester scope")
        row = audit.query(tool="action_propose")[0]
        assert row["kind"] == "rest:action_propose" and row["actor"]["role"] == "tester"
        assert audit.verify()["ok"]
    finally:
        service.set_audit_log(None)
        engmod.set_engine(None)
