"""Build step 1 (docs/DEPLOY-DESIGN.md): callers and audit rows carry `role` and `lease_id`, and
REST approvals, rejections and cancels reach the hash-chained audit log."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_engine import box, make_engine  # noqa: E402,F401

from shimpaku import service  # noqa: E402
from shimpaku.actions import engine as engmod  # noqa: E402
from shimpaku.actions.engine import Caller  # noqa: E402
from shimpaku.api.auth import Principal  # noqa: E402
from shimpaku.mcp.audit import AuditLog  # noqa: E402

OPERATOR_REST = {"kind": "dashboard", "name": "admin", "transport": "rest", "role": "operator"}


@pytest.fixture
def wired(tmp_path, box):  # noqa: F811
    e = make_engine(tmp_path, box)
    engmod.set_engine(e)
    audit = AuditLog(tmp_path / "state.db", tmp_path / "audit.jsonl")
    service.set_audit_log(audit)
    yield e, audit
    service.set_audit_log(None)
    engmod.set_engine(None)


def test_caller_role_defaults_and_round_trip():
    assert Caller(kind="dashboard", name="rohan").role == "operator"
    assert Caller(kind="mcp", name="cc").role == "agent"
    assert Caller(kind="system", name="x").role == "system"
    assert Caller(kind="weird", name="x").role == "visitor"
    # an explicit but unknown role grants nothing, not the kind's default
    assert Caller(kind="dashboard", name="x", role="admin").role == "visitor"
    t = Caller(kind="dashboard", name="tester:p1", role="tester", lease_id="L1")
    d = t.as_dict()
    assert d["role"] == "tester" and d["lease_id"] == "L1"
    assert Caller.coerce(d) == t
    assert Caller(kind="dashboard", name="a").as_dict()["lease_id"] is None  # nullable, always present


def test_principal_caller_carries_role_and_lease():
    assert Principal("session", "admin", "admin").caller()["role"] == "operator"
    assert Principal("token", "token:cc", "propose", token_id="t").caller()["role"] == "agent"
    assert Principal("anonymous", "visitor", "read").caller()["role"] == "visitor"
    c = Principal("session", "tester:p1", "admin", role="tester", lease_id="L1").caller()
    assert c["role"] == "tester" and c["lease_id"] == "L1"


def test_rest_approve_reject_cancel_are_audited_and_old_rows_still_verify(wired):
    """Before this step a REST approval was recorded only in the approvals table, never in the
    hash-chained audit log."""
    e, audit = wired
    # a row written before role/lease existed: must verify unchanged
    old = audit.state.audit_append(actor=json.dumps({"kind": "mcp", "name": "old"}), kind="mcp:http:tools/call",
                                   tool="inventory_list", args={}, result={}, status="ok")
    agent = Caller(kind="mcp", name="cc", transport="stdio")
    a = e.propose("bounce_port", {"device": "spine2", "ifname": "ge-0/0/9"}, "lab test", agent)
    b = e.propose("disable_port", {"device": "leaf2", "ifname": "ge-0/0/3"}, "lab test", agent)
    asyncio.run(service.action_approve(a["id"], OPERATOR_REST))
    asyncio.run(service.action_reject(b["id"], OPERATOR_REST))
    c = asyncio.run(service.action_propose("bounce_port", {"device": "leaf2", "ifname": "ge-0/0/4"}, "x",
                                           {**OPERATOR_REST}))
    asyncio.run(service.action_cancel(c["id"], OPERATOR_REST))
    rows = {r["tool"]: r for r in audit.query(limit=50)}
    for tool, aid in (("action_approve", a["id"]), ("action_reject", b["id"]),
                      ("action_propose", c["id"]), ("action_cancel", c["id"])):
        r = rows[tool]
        assert r["kind"] == f"rest:{tool}" and r["action_id"] == aid
        assert r["actor"]["role"] == "operator" and "lease_id" in r["actor"] and r["actor"]["lease_id"] is None
    assert rows["action_approve"]["status"] == "approved"
    assert audit.verify() == {"ok": True, "rows": len(audit.query(limit=1000))}
    assert audit.query(limit=1000)[-1]["seq"] == old["seq"]  # history untouched


def test_refused_rest_approval_is_audited(wired):
    e, audit = wired
    a = e.propose("bounce_port", {"device": "spine2", "ifname": "ge-0/0/9"}, "lab test",
                  Caller(kind="mcp", name="cc", transport="stdio"))
    with pytest.raises(PermissionError):
        asyncio.run(service.action_approve(a["id"], {"kind": "mcp", "name": "token:x", "transport": "rest"}))
    r = audit.query(tool="action_approve")[0]
    assert r["status"] == "refused" and r["actor"]["role"] == "agent" and r["action_id"] == a["id"]
    assert audit.verify()["ok"]


def test_mcp_writes_are_not_double_audited(wired):
    e, audit = wired
    asyncio.run(service.action_propose("bounce_port", {"device": "spine2", "ifname": "ge-0/0/9"}, "x",
                                       Caller(kind="mcp", name="cc", transport="http")))
    assert audit.query(tool="action_propose") == []  # the MCP middleware writes that row
