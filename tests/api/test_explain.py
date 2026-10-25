"""/api/explain/* (routes_explain): funnel paths from stored actions, the audit chain walk and the
in-memory tamper test, the blast-radius → map mapping, and guest safety."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from shimpaku import service
from shimpaku.api import demo, routes_explain as rx
from shimpaku.api.app import create_app
from shimpaku.mcp.audit import AuditLog
from shimpaku.store import StateDB


def _tl(*to: str) -> list[dict]:
    return [{"ts": 1, "from": "x", "to": t, "note": ""} for t in to]


def test_path_of_every_branch():
    assert rx.path_of({"status": "blocked", "policy": {"reasons": ["G6 a reason is required", "G0 x"]}}) == \
        ["proposed", "blocked:G6"]
    assert rx.path_of({"status": "blocked", "policy": {"reasons": ["T0 a visitor cannot propose"]}})[-1] == "blocked:T0"
    assert rx.path_of({"status": "awaiting_approval", "timeline": _tl("awaiting_approval")}) == \
        ["proposed", "passed", "awaiting", "waiting"]
    assert rx.path_of({"status": "rejected", "timeline": _tl("awaiting_approval", "rejected")})[-1] == "rejected"
    assert rx.path_of({"status": "verified", "commits": [{}],
                       "timeline": _tl("awaiting_approval", "approved", "running", "validating", "verified")}) == \
        ["proposed", "passed", "awaiting", "approved", "executed", "verified"]
    assert rx.path_of({"status": "recurred", "timeline": _tl("automatic", "running", "validating", "recurred")}) == \
        ["proposed", "passed", "automatic", "executed", "recurred"]
    rb = {"status": "failed", "timeline": [*_tl("awaiting_approval", "approved", "running"),
                                           {"to": "failed", "note": "executor died; Junos rolled back by itself"}]}
    assert rx.path_of(rb)[-1] == "rolled_back"
    assert rx.path_of({"status": "approved", "timeline": _tl("awaiting_approval", "approved")})[-1] == "queued"


def test_caller_kind_never_uses_the_name():
    assert rx.caller_kind({"kind": "assistant", "role": "tester", "name": "shim:alice"}) == "assistant"
    assert rx.caller_kind({"kind": "dashboard", "role": "tester"}) == "tester"
    assert rx.caller_kind({"kind": "dashboard", "role": "operator"}) == "operator"
    assert rx.caller_kind({"kind": "mcp", "role": "agent"}) == "agent"
    assert rx.caller_kind({"kind": "system"}) == "other"


def test_funnel_counts_filters_and_scrubs():
    now = time.time()
    rows = [
        {"id": "a1", "status": "blocked", "created": now - 60, "caller": {"kind": "dashboard", "role": "tester", "name": "bob"},
         "policy": {"reasons": ["G5 sw1 is leased by alice@example.com (lease L-9)"]}},
        {"id": "a2", "status": "blocked", "created": now - 60, "caller": {"kind": "mcp", "role": "agent"},
         "policy": {"reasons": ["G7 10.99.0.21 did not answer"]}},
        {"id": "a3", "status": "verified", "created": now - 2 * 86400, "caller": {"kind": "mcp", "role": "agent"},
         "commits": [{}], "timeline": _tl("awaiting_approval", "approved", "running", "validating", "verified")},
    ]
    f = rx.funnel(rows, "all", 0, now)
    nodes = {n["id"]: n for n in f["nodes"]}
    assert f["total"] == 3 and nodes["proposed"]["count"] == 3 and nodes["verified"]["ids"] == ["a3"]
    blob = json.dumps(f)
    assert "alice" not in blob and "bob" not in blob and "10.99.0.21" not in blob
    assert nodes["blocked:G5"]["examples"] == ["G5 sw1 is leased by another session"]
    assert {(lk["source"], lk["target"]) for lk in f["links"]} >= {("proposed", "blocked:G5"), ("executed", "verified")}
    day = rx.funnel(rows, "agent", 86400, now)
    assert day["total"] == 1 and day["by_caller"]["tester"] == 1


def test_funnel_route_demo_and_validation():
    c = TestClient(create_app(demo, auth_mode="off"))
    r = c.get("/api/explain/funnel", params={"window": "all"}).json()
    assert r["demo"] is True and r["total"] >= 10
    stages = {n["id"] for n in r["nodes"]}
    assert {"blocked:G1", "blocked:T0", "rejected", "cancelled", "verified", "recurred", "rolled_back"} <= stages
    # every id in a band is an action the actions list knows
    ids = {a["id"] for a in c.get("/api/actions").json()["actions"]}
    assert all(set(n["ids"]) <= ids for n in r["nodes"])
    assert c.get("/api/explain/funnel", params={"caller": "root"}).status_code == 422
    assert c.get("/api/explain/funnel", params={"window": "1y"}).status_code == 422


def _audit(tmp_path, n=12) -> AuditLog:
    log = AuditLog(StateDB(tmp_path / "s.db").init(), tmp_path / "audit.jsonl")
    for i in range(n):
        log.append(caller={"kind": "mcp", "name": f"secret-user-{i}", "token_id": "tok_abc"}, transport="http",
                   tool="sle_get_summary", args={"site": "campus"})
    return log


def test_audit_chain_and_tamper_on_a_copy(tmp_path, monkeypatch):
    log = _audit(tmp_path)
    monkeypatch.setattr(service, "_audit", log)
    c = TestClient(create_app(service, auth_mode="off"))
    page = c.get("/api/explain/audit/chain", params={"limit": 5}).json()
    assert [b["seq"] for b in page["blocks"]] == [12, 11, 10, 9, 8] and all(b["ok"] for b in page["blocks"])
    assert page["next_before"] == 8 and page["verify"]["ok"] is True and page["total"] == 12
    last = c.get("/api/explain/audit/chain", params={"before": 3, "limit": 5}).json()
    assert [b["seq"] for b in last["blocks"]] == [2, 1] and last["next_before"] is None and all(b["ok"] for b in last["blocks"])
    blob = json.dumps(page)
    assert "secret-user" not in blob and "tok_abc" not in blob and "campus" not in blob
    before = log.state.audit_query(limit=100)
    t = c.get("/api/explain/audit/tamper", params={"seq": 6}).json()
    assert t["first_bad_seq"] == 6 and t["copy_only"] is True and "row_hash" in t["why"]
    t2 = c.get("/api/explain/audit/tamper", params={"seq": 6, "rehash": True}).json()
    assert t2["first_bad_seq"] == 7 and "prev_hash" in t2["why"]
    assert log.state.audit_query(limit=100) == before and log.verify()["ok"] is True   # real log untouched
    assert c.get("/api/explain/audit/tamper", params={"seq": 99}).status_code == 404


def test_audit_chain_demo_hashes_are_real():
    c = TestClient(create_app(demo, auth_mode="off"))
    page = c.get("/api/explain/audit/chain").json()
    assert page["demo"] and page["verify"]["ok"] and all(b["ok"] for b in page["blocks"])
    assert c.get("/api/explain/audit/tamper", params={"seq": 40}).json()["first_bad_seq"] == 40


def test_audit_chain_unavailable(monkeypatch):
    monkeypatch.setattr(service, "_audit", None)
    c = TestClient(create_app(service, auth_mode="off"))
    assert c.get("/api/explain/audit/chain").status_code == 503


def test_map_entities_pairs_ports_with_their_node():
    links = [{"id": "leaf1-spine2", "a": "leaf1", "a_if": "ge-0/0/1", "b": "spine2", "b_if": "ge-0/0/0"},
             {"id": "leaf2-spine2", "a": "leaf2", "a_if": "ge-0/0/1", "b": "spine2", "b_if": "ge-0/0/1"},
             {"id": "leaf1-spine1", "a": "leaf1", "a_if": "ge-0/0/0", "b": "spine1", "b_if": "ge-0/0/0"}]
    nodes = {"leaf1", "leaf2", "spine1", "spine2"}
    m = rx.map_entities([("root cause", "leaf1:ge-0/0/1.0:ospf"), ("blast radius", "probe:c1->x")], nodes, links)
    assert [n["name"] for n in m["nodes"]] == ["leaf1"] and [lk["id"] for lk in m["links"]] == ["leaf1-spine2"]
    assert m["unmapped"] == ["probe:c1->x"]


def test_blast_map_route_real_shapes():
    async def incident_get(iid):
        return {"incident_id": iid, "root_causes": [{"entity": "sw1:ge-0/0/0", "fault_class": "link-down"}]}

    async def sle_get_blast_radius(iid):
        return {"incident_id": iid, "blast_radius": {"affected": ["r1", "probe:h1->dns"]}, "root_causes": []}

    async def topo_get_neighbors(device=None):
        return {"intended": [{"device": "r1", "ifname": "ge-0/0/1", "peer": "sw1", "link": "r1-sw1"},
                             {"device": "sw1", "ifname": "ge-0/0/0", "peer": "r1", "link": "r1-sw1"}]}

    async def inventory_list(site=None):
        return {"nodes": [{"name": "r1", "mgmt_ip": "10.99.0.11"}, {"name": "sw1", "mgmt_ip": "10.99.0.21"}]}

    svc = SimpleNamespace(incident_get=incident_get, sle_get_blast_radius=sle_get_blast_radius,
                          topo_get_neighbors=topo_get_neighbors, inventory_list=inventory_list)
    r = TestClient(create_app(svc, auth_mode="off")).get("/api/explain/incidents/INC-1/blast-map")
    d = r.json()
    assert r.status_code == 200 and "10.99" not in r.text
    assert {n["name"] for n in d["nodes"]} == {"sw1", "r1"} and [lk["id"] for lk in d["links"]] == ["r1-sw1"]


def test_tool_event_args_summary_is_redacted():
    s = service._args_summary({"device": "sw1", "api_token": "abc", "host": "10.1.2.3"})
    assert "abc" not in s and "10.1.2.3" not in s and "device=sw1" in s
    assert service._args_summary(None) == ""
