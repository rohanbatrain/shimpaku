"""Theatre mode (/api/theatre*, routes_theatre): stage order and real timestamps, run selection,
the demo state machine (api/demo_theatre.py), visitor read-only replay and guest safety."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from shimpaku.actions import fixture
from shimpaku.api import demo, demo_theatre, routes_theatre
from shimpaku.api.app import create_app

T = 1_800_000_000.0
ORDER = [k for k, _ in routes_theatre.STAGES]


def _raw(status="verified", **kw):
    tl = [{"ts": T + 30.4, "from": "open", "to": "awaiting_approval", "note": ""},
          {"ts": T + 50, "from": "awaiting_approval", "to": "approved", "note": "approved by admin"},
          {"ts": T + 51, "from": "approved", "to": "running", "note": "executor picked up"},
          {"ts": T + 53.2, "from": "running", "to": "running", "note": "commit: shimpaku a1 add vlan 999"},
          {"ts": T + 55.4, "from": "running", "to": "running",
           "note": "fresh-session health check: {'reachable': True, 'pending_confirm': True, 'health': {'ospf_full': 0}, 'ok': True}"},
          {"ts": T + 57, "from": "running", "to": "validating", "note": "confirmed; validating for 180 s"}]
    if status in ("verified", "recurred"):
        tl.append({"ts": T + 237, "from": "validating", "to": status, "note": "x"})
    a = {"id": "a1", "kind": fixture.KIND, "args": dict(fixture.ARGS), "status": status, "created": T + 30,
         "caller": {"kind": "assistant", "name": "shim:admin", "role": "agent", "token_id": "tok_secret",
                    "client": "10.99.0.5"},
         "policy": {"checked": ["G0", "G1", "G3", "G5"], "reasons": []}, "timeline": tl,
         "commits": [{"leg": "add vlan 999", "confirm_min": 2, "seconds": 2.2, "ts": T + 53.2,
                      "junos_index0_time": "2027-01-15 08:00:53 UTC", "comment": "shimpaku a1 add vlan 999"},
                     {"leg": "confirm", "confirm_min": None, "seconds": 1.1, "ts": T + 56.8,
                      "junos_index0_time": "2027-01-15 08:00:56 UTC"}],
         "validation": {"starts": T + 57, "ends": T + 237, "checks": [{"ts": T + 87, "ok": True}]}}
    a.update(kw)
    return a


BREAKS = [{"t": T, "by_role": "operator", "by_kind": "cli"}]


def test_stages_in_order_with_recorded_times():
    run = routes_theatre.build_run(_raw(), BREAKS, None, sle={"available": False}, incidents={"incidents": []},
                                   now=T + 300)
    st = {s["id"]: s for s in run["stages"]}
    assert [s["id"] for s in run["stages"]] == ORDER
    assert run["mode"] == "replay"
    assert st["break"]["t"] == T and st["propose"]["t"] == T + 30 and st["approve"]["t"] == T + 50
    assert st["commit"]["t"] == T + 53.2 and st["commit"]["junos_time"] == "2027-01-15 08:00:53 UTC"
    assert st["health"]["t"] == T + 55.4 and st["health"]["state"] == "done"
    assert st["confirm"]["t"] == T + 56.8
    assert st["validate"]["starts"] == T + 57 and st["validate"]["ends"] == T + 237
    assert st["outcome"]["t"] == T + 237 and st["outcome"]["label"] == "Verified"
    # nothing that did not happen is given a time
    assert st["dip"]["state"] == "skipped" and st["dip"]["t"] is None
    assert st["incident"]["state"] == "skipped" and st["incident"]["t"] is None
    assert st["approve"]["by"] == {"name": "admin"}
    assert st["propose"]["by"] == {"name": "shim:admin", "kind": "assistant", "role": "agent"}
    blob = json.dumps(run)
    assert "tok_secret" not in blob and "10.99" not in blob and "ospf_full" not in blob


def test_live_run_marks_current_and_dip_and_incident():
    a = _raw("validating")
    sle = {"sle": {"connect": {"sle": "connect", "pct": 90}},
           "series": {"connect": [{"t": T - 60, "value": 50}, {"t": T + 20, "value": 80, "n": 10}]}}
    incs = {"incidents": [{"id": "INC-9", "title": "trunk sw1 lost vlan", "opened_at": T + 25, "status": "open"}]}
    run = routes_theatre.build_run(a, BREAKS, None, sle=sle, incidents=incs, now=T + 100,
                                   fixture_state={"present": True, "reason": None})
    st = {s["id"]: s for s in run["stages"]}
    assert run["mode"] == "live"
    assert st["dip"]["t"] == T + 20 and "80" in st["dip"]["detail"]          # first dip after the break only
    assert st["incident"]["t"] == T + 25 and st["incident"]["detail"].startswith("INC-9")
    assert st["validate"]["state"] == "current" and st["outcome"]["state"] == "pending"
    assert run["fixture"] == {"present": True, "reason": None, "live": True}


def test_recurred_names_the_refault():
    br = [{"t": T + 100, "by_role": "tester", "by_kind": "dashboard"}] + BREAKS
    run = routes_theatre.build_run(_raw("recurred"), br, None, now=T + 300)
    out = run["stages"][-1]
    assert out["label"] == "Recurred" and out["state"] == "failed" and out["refault_t"] == T + 100
    assert run["stages"][0]["t"] == T     # the run's own fault, not the re-injection


def test_failed_action_marks_the_stage_it_stopped_at():
    a = _raw("failed", commits=[], timeline=[
        {"ts": T + 30.4, "from": "open", "to": "awaiting_approval", "note": ""},
        {"ts": T + 50, "from": "awaiting_approval", "to": "approved", "note": "approved by admin"},
        {"ts": T + 52, "from": "approved", "to": "failed", "note": "G5"}],
        validation=None, error="G5 refused: device 10.99.0.21 busy")
    st = {s["id"]: s for s in routes_theatre.build_run(a, BREAKS, None, now=T + 60)["stages"]}
    assert st["commit"]["state"] == "failed" and st["commit"]["t"] == T + 52
    assert "10.99" not in st["commit"]["detail"] and "<host>" in st["commit"]["detail"]


def test_break_without_proposal_is_a_pending_run():
    run = routes_theatre.build_run(None, BREAKS, None, now=T + 10)
    st = {s["id"]: s for s in run["stages"]}
    assert run["mode"] == "pending" and st["break"]["state"] == "done" and st["propose"]["state"] == "current"


def test_older_fault_is_not_claimed_by_a_later_run():
    run = routes_theatre.build_run(_raw(), BREAKS, T + 10, now=T + 300)   # a previous run started at T+10
    assert run["stages"][0]["state"] == "skipped"


# ---- through the API (demo service) ---------------------------------------------------
@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(routes_theatre, "RUN_TTL_S", 0.0)
    monkeypatch.setattr(routes_theatre, "FIXTURE_TTL_S", 0.0)
    demo_theatre.reset()
    yield
    demo_theatre.reset()


def _csrf(c):
    c.get("/api/whoami")
    return {"X-CSRF-Token": c.cookies.get("shimpaku_csrf"), "Origin": str(c.base_url).rstrip("/")}


def _shift(aid, seconds):
    """Move a demo run (its action and the faults since just before it) back in time."""
    a = demo_theatre._state["actions"][aid]
    for b in demo_theatre._state["breaks"]:
        if b["t"] >= a["created"] - 5:
            b["t"] -= seconds
    a["created"] -= seconds
    a["approved_at"] -= seconds


def test_presenter_flow_break_propose_approve_verify():
    c = TestClient(create_app(demo, auth_mode="off"))
    h = _csrf(c)
    r = c.get("/api/theatre").json()
    assert r["mode"] == "replay" and r["demo"] is True and r["action"]["id"] == "act-demo-replay"
    assert c.post("/api/demo/break", headers=h).json()["changed"] is True
    routes_wait = c.get("/api/theatre").json()
    assert routes_wait["mode"] == "pending"
    a = c.post("/api/actions", json={"kind": fixture.KIND, "args": fixture.ARGS, "reason": "x"}, headers=h).json()
    assert a["status"] == "awaiting_approval"
    assert c.post(f"/api/actions/{a['id']}/approve", headers=h).status_code == 200
    _shift(a["id"], 100)
    r = c.get("/api/theatre").json()
    st = {s["id"]: s for s in r["stages"]}
    assert r["action"]["id"] == a["id"] and r["action"]["status"] == "verified"
    assert all(st[k]["t"] for k in ("break", "propose", "guardrails", "approve", "commit", "health", "confirm", "validate", "outcome"))
    ts = [st[k]["t"] for k in ("break", "approve", "commit", "health", "confirm", "validate", "outcome")]
    assert ts == sorted(ts)
    assert c.get(f"/api/theatre?action={a['id']}").json()["action"]["id"] == a["id"]
    assert [x["id"] for x in c.get("/api/theatre/runs").json()["runs"]][:2] == [a["id"], "act-demo-replay"]


def test_break_during_validation_recurs_in_demo():
    c = TestClient(create_app(demo, auth_mode="off"))
    h = _csrf(c)
    c.post("/api/demo/break", headers=h)
    a = c.post("/api/actions", json={"kind": fixture.KIND, "args": fixture.ARGS, "reason": "x"}, headers=h).json()
    c.post(f"/api/actions/{a['id']}/approve", headers=h)
    _shift(a["id"], 9)   # now validating, VLAN back
    assert c.post("/api/demo/break", headers=h).json()["changed"] is True
    _shift(a["id"], demo_theatre.CHECK_EVERY_S + 1)
    assert c.get(f"/api/actions/{a['id']}").json()["status"] == "recurred"


def test_second_proposal_while_one_is_live_is_blocked_g5():
    c = TestClient(create_app(demo, auth_mode="off"))
    h = _csrf(c)
    body = {"kind": fixture.KIND, "args": fixture.ARGS, "reason": "x"}
    assert c.post("/api/actions", json=body, headers=h).json()["status"] == "awaiting_approval"
    assert c.post("/api/actions", json=body, headers=h).json()["status"] == "blocked"


def test_agent_propose_prompt_creates_an_assistant_action():
    c = TestClient(create_app(demo, auth_mode="off"))
    with c.stream("POST", "/api/assistant/ask", json={"question": "Propose the fix for VLAN 999 on sw1 ge-0/0/5"},
                  headers=_csrf(c)) as r:
        body = "".join(r.iter_text())
    assert "action_propose_add_vlan_to_trunk" in body
    newest = c.get("/api/theatre/runs").json()["runs"][0]
    a = c.get(f"/api/actions/{newest['id']}").json()
    assert a["proposed_by"].startswith("shim:") and a["status"] == "awaiting_approval"


def test_non_fixture_action_is_404():
    c = TestClient(create_app(demo, auth_mode="off"))
    assert c.get("/api/theatre?action=42").status_code == 404


def test_visitor_reads_a_completed_replay_and_cannot_write():
    c = TestClient(create_app(demo, auth_mode="local", public_view=True))
    h = _csrf(c)
    # a presenter's live run exists, but a visitor is shown the most recent completed run
    demo_theatre._state["actions"]["act-demo-live"] = demo_theatre._new("act-demo-live", __import__("time").time(),
                                                                        {"kind": "mcp", "name": "x", "role": "agent"})
    r = c.get("/api/theatre")
    assert r.status_code == 200 and r.json()["action"]["id"] == "act-demo-replay" and r.json()["mode"] == "replay"
    assert c.post("/api/demo/break", headers=h).status_code in (401, 403)
    assert c.post("/api/actions/act-demo-live/approve", headers=h).status_code in (401, 403)
    blob = c.get("/api/theatre").text
    assert "mgmt_ip" not in blob and "config" not in blob


def test_stream_emits_a_theatre_event():
    c = TestClient(create_app(demo, auth_mode="off"))
    gen = routes_theatre  # noqa: F841
    with c.stream("GET", "/api/stream/theatre") as r:
        for line in r.iter_lines():
            if line.startswith("event:"):
                assert line.split(":", 1)[1].strip() == "theatre"
                break
