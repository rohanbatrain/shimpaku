"""/api/virga/* (docs/build/virga-live.md §2): who may inject, revert and recover; busy, needs-recovery
and demo-set refusals; the replay's refusal; the allow-listed state; the canned simulated trial; the
public drill block and /api/public/health; Theatre's "Fault injected" stage for a Virga drill.
In-process with a fake runner (``execute``) and a fake catalogue: no lab."""

from __future__ import annotations

import json
import re
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bench"))
sys.path.insert(0, str(ROOT / "tests" / "actions"))
from test_engine import box  # noqa: E402,F401
from test_tester_passes import client, csrf, invite, operator, redeem, world  # noqa: E402,F401

from shimpaku import virga_live as vl  # noqa: E402
from shimpaku.api import demo, replay  # noqa: E402
from shimpaku.api.app import create_app  # noqa: E402
from shimpaku.api.demo_virga import DemoVirga  # noqa: E402
from shimpaku.api.ratelimit import RateLimiter  # noqa: E402
from shimpaku.api.routes_public import site_health  # noqa: E402
from shimpaku.api.routes_theatre import break_rows, build_run  # noqa: E402

IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
LAB = {"nodes": {"leaf1": {"mgmt": "10.99.0.41"}}, "allocation": {"write": ["leaf1"]}, "vars": {},
       "timing_defaults": {"poll_s": 4, "stable": 2, "inject_ttl_min": 5}, "health_sets": {}}


def _sc(sid, **over):
    sc = {"id": sid, "title": f"{sid} drill", "layer": "fabric-underlay", "status": "selftested", "executable": True,
          "requires": ["day-06-fabric"], "probes": [],
          "inject": [{"junos": {"node": "leaf1", "lines": ["set interfaces ge-0/0/0 disable"]}}],
          "revert": [{"junos": {"node": "leaf1", "lines": ["delete interfaces ge-0/0/0 disable"]}}],
          "ground_truth": {"fault_class": "link-down"}}
    sc.update(over)
    return sc


def fake_cat():
    scs = [_sc("FAB-01"), _sc("CAM-06", requires=["day-02-campus"], layer="mgmt"), _sc("FAB-04", status="executable"),
           _sc("HLD-01"), _sc("CAM-01", executable=False, status="designed", requires=["day-02-campus", "aiops-farm"])]
    return SimpleNamespace(lab=LAB, scenarios={s["id"]: s for s in scs}, sha256="t", holdout_ids={"HLD-01"})


class Gate:
    """A runner stand-in: walks the phases, holds until revert now, journals like the real one."""

    def __init__(self):
        self.started = threading.Event()
        self.calls: list[str] = []

    def execute(self, ctx, sc, trial_id=None, **_):
        from virga.runner import emit, mark, say
        self.calls.append(sc["id"])
        mark(ctx, "preflight", "running")
        mark(ctx, "preflight", "ok", "baseline healthy")
        mark(ctx, "inject", "running")
        ctx.journal.put(trial_id, {"scenario": sc["id"], "nodes": ["leaf1"], "write_nodes": ["leaf1"]})
        say(ctx, "    inject: set interfaces ge-0/0/0 disable on 10.99.0.41")
        mark(ctx, "inject", "ok", "1 commit(s) on leaf1", changed=True)
        mark(ctx, "verify_injected", "ok", "fault confirmed")
        mark(ctx, "hold", "running", "held", hold_until=time.time() + ctx.hold_s)
        self.started.set()
        ctx.revert_now.wait(10)
        for p in ("hold", "revert", "verify_reverted"):
            mark(ctx, p, "ok", "done")
        ctx.journal.remove(trial_id)
        emit(ctx, type="done", verdict="pass")
        return {"trial_id": trial_id, "verdict": "pass",
                "phases": {"verify_injected": {"time_to_effect_s": 1.5}, "verify_reverted": {"time_to_restore_s": 6.0}}}


@pytest.fixture
def lab(world, tmp_path):  # noqa: F811
    gate = Gate()
    m = vl.VirgaLive(enabled=True, store=tmp_path / "store", publish=tmp_path / "pub", catalogue=fake_cat(),
                     lab_factory=lambda c, h: SimpleNamespace(drop=lambda *a: None),
                     ports=lambda: {("leaf1", "ge-0/0/0"): "spine1"}, holders=lambda: [], execute=gate.execute,
                     recover=lambda ctx, j, wait: [j.remove(k) for k in list(j.entries())] and 0)
    world.app.state.virga_live = m
    return SimpleNamespace(m=m, gate=gate, w=world)


def _rows(w, tool):
    return [r for r in w.audit.query(limit=1000) if r.get("tool") == tool]


def _until(fn, s=5.0):
    t = time.time() + s
    while time.time() < t:
        if fn():
            return True
        time.sleep(0.02)
    raise AssertionError("timed out")


# ================================================================ the operator's drill

def test_operator_injects_everyone_watches_revert_ends_the_hold(lab):
    c, h = operator(lab.w)
    r = c.post("/api/virga/trials", json={"scenario": "FAB-01", "hold_s": 120}, headers=h)
    assert r.status_code == 202, r.text
    tid = r.json()["trial_id"]
    assert lab.gate.started.wait(5)
    st = c.get("/api/virga/state").json()
    assert st["busy"] and st["enabled"] and st["simulated"] is False and st["trial"]["id"] == tid
    t = st["trial"]
    assert t["phase"] == "hold" and t["hold_until"] > t["t_start"] and t["by_role"] == "operator"
    assert t["links"] == [["leaf1", "spine1"]] and [p["name"] for p in t["phases"]] == vl.PHASES
    assert c.post("/api/virga/trials", json={"scenario": "FAB-01"}, headers=h).status_code == 409  # one at a time
    assert c.post("/api/virga/trials/nope/revert", headers=h).status_code == 404
    assert c.post(f"/api/virga/trials/{tid}/revert", headers=h).json() == {"ok": True}
    _until(lambda: not lab.m.state()["busy"])
    st = c.get("/api/virga/state").json()
    assert st["trial"]["verdict"] == "pass" and st["trial"]["phase"] == "done"
    assert st["recent"][0] == {**st["recent"][0], "id": tid, "verdict": "pass", "time_to_effect_s": 1.5,
                               "time_to_restore_s": 6.0}
    assert c.get("/api/virga/trials").json()["trials"][0]["id"] == tid
    inj = _rows(lab.w, "virga_inject")
    assert [r["status"] for r in inj] == ["refused:busy", "changed"]
    assert inj[1]["args"]["scenario"] == "FAB-01" and inj[1]["args"]["nodes"] == ["leaf1"]
    assert inj[1]["actor"]["role"] == "operator"
    assert [r["status"] for r in _rows(lab.w, "virga_revert")] == ["ok", "refused:not_found"]
    assert lab.w.audit.verify()["ok"]
    # Theatre's "Fault injected" stage lights up from the virga_inject row
    brk = break_rows({"rows": lab.w.audit.query(limit=50)})
    assert brk[0]["virga"] == {"scenario": "FAB-01", "nodes": ["leaf1"]}
    run = build_run(None, brk, None)
    assert run["stages"][0]["state"] == "done" and "Virga fault drill FAB-01 on leaf1" in run["stages"][0]["detail"]


def test_state_and_history_are_allow_listed(lab):
    c, h = operator(lab.w)
    tid = c.post("/api/virga/trials", json={"scenario": "FAB-01"}, headers=h).json()["trial_id"]
    assert lab.gate.started.wait(5)
    c.post(f"/api/virga/trials/{tid}/revert", headers=h)
    _until(lambda: not lab.m.state()["busy"])
    for path in ("/api/virga/state", "/api/virga/trials", "/api/virga/catalogue"):
        blob = c.get(path).text
        assert "set interfaces" not in blob and not IPV4.search(blob) and "mgmt_ip" not in blob, path
        assert '"admin"' not in blob and "ground_truth" not in blob and "link-down" not in blob, path
    log = c.get("/api/virga/state").json()["trial"]["log"]
    assert any(x["msg"] == "inject: <config>" for x in log)  # the statement and the address after it are gone


# ================================================================ who may write

def _attempts(c, h):
    return [c.post("/api/virga/trials", json={"scenario": "FAB-01"}, headers=h).status_code,
            c.post("/api/virga/trials/x/revert", headers=h).status_code,
            c.post("/api/virga/recover", headers=h).status_code]


def test_anonymous_cannot_write(lab):
    c = client(lab.w)
    assert set(_attempts(c, csrf(c))) <= {401, 403}
    assert lab.gate.calls == []


@pytest.mark.parametrize("scope", ["read", "propose"])
def test_operator_tokens_are_refused_whatever_the_scope(lab, scope):
    tok = lab.w.auth.create_token("agent", scope, "admin")["token"]
    assert _attempts(client(lab.w), {"Authorization": f"Bearer {tok}"}) == [403, 403, 403]
    assert lab.gate.calls == []
    assert {r["status"] for r in _rows(lab.w, "virga_inject")} == {"refused:not_operator"}
    assert _rows(lab.w, "virga_inject")[0]["actor"]["role"] == "agent"


def test_a_forged_operator_role_token_is_refused(lab):
    tok = lab.w.auth.create_token("forged", "propose", "admin", role="operator")["token"]
    assert _attempts(client(lab.w), {"Authorization": f"Bearer {tok}"}) == [403, 403, 403]
    assert lab.gate.calls == []


def test_tester_session_and_lease_token_are_refused(lab):
    t, r = redeem(lab.w, invite(lab.w))
    assert r.status_code == 200
    assert _attempts(t, csrf(t)) == [403, 403, 403]
    tok = t.post("/api/lease/token", headers=csrf(t)).json()["token"]
    assert _attempts(client(lab.w), {"Authorization": f"Bearer {tok}"}) == [403, 403, 403]
    assert lab.gate.calls == []
    assert any(r["actor"]["role"] == "tester" for r in _rows(lab.w, "virga_inject"))


def test_public_visitor_watches_but_cannot_write(lab):
    from shimpaku import service
    app = create_app(service, auth_mode="local", web_dist=lab.w.tmp / "dist", leases=lab.w.lm,
                     limiter=RateLimiter({}), public_view=True, totp=lab.w.totp)
    app.state.virga_live = lab.m
    c = TestClient(app)
    h = csrf(c)
    assert c.get("/api/whoami").json()["role"] == "visitor"
    assert c.get("/api/virga/state").status_code == 200 and c.get("/api/virga/catalogue").status_code == 200
    assert _attempts(c, h) == [403, 403, 403]
    assert lab.gate.calls == []


def test_cross_site_post_is_refused(lab):
    c, h = operator(lab.w)
    r = c.post("/api/virga/trials", json={"scenario": "FAB-01"}, headers={**h, "Origin": "https://evil.example"})
    assert r.status_code == 403 and lab.gate.calls == []


# ================================================================ refusals: demo set, recovery, rate

@pytest.mark.parametrize("body", [{"scenario": "CAM-06"}, {"scenario": "FAB-04"}, {"scenario": "HLD-01"},
                                  {"scenario": "CAM-01"}, {"scenario": "NOPE-99"}, {},
                                  {"scenario": "FAB-01", "hold_s": 5}, {"scenario": "FAB-01", "hold_s": 601}])
def test_outside_the_demo_set_is_422(lab, body):
    c, h = operator(lab.w)
    r = c.post("/api/virga/trials", json=body, headers=h)
    assert r.status_code == 422, r.text
    assert lab.gate.calls == [] and _rows(lab.w, "virga_inject")[0]["status"].startswith("refused:")


def test_catalogue_marks_the_demo_set_and_hides_the_holdout(lab):
    c, _ = operator(lab.w)
    cat = c.get("/api/virga/catalogue").json()
    assert cat["hold"] == {"default_s": 180, "max_s": 600} and cat["enabled"] and cat["simulated"] is False
    rows = {s["id"]: s for s in cat["scenarios"]}
    assert "HLD-01" not in rows
    assert rows["FAB-01"]["demo_ok"] and not rows["CAM-06"]["demo_ok"] and not rows["FAB-04"]["demo_ok"]
    assert rows["CAM-06"]["site"] == "campus" and rows["FAB-01"]["site"] == "fabric"


def test_needs_recovery_refuses_injects_until_recover(lab):
    lab.m.journal.put("FAB-01-crashed", {"scenario": "FAB-01", "nodes": ["leaf1"], "write_nodes": ["leaf1"]})
    c, h = operator(lab.w)
    assert c.get("/api/virga/state").json()["needs_recovery"] is True
    r = c.post("/api/virga/trials", json={"scenario": "FAB-01"}, headers=h)
    assert r.status_code == 409 and "Recover" in r.json()["detail"]
    r = c.post("/api/virga/recover", headers=h)
    assert r.status_code == 200 and r.json() == {"recovered": ["FAB-01-crashed"], "ok": True}
    assert c.get("/api/virga/state").json()["needs_recovery"] is False
    assert [x["status"] for x in _rows(lab.w, "virga_recover")] == ["ok"]
    assert c.post("/api/virga/trials", json={"scenario": "FAB-01"}, headers=h).status_code == 202


def test_injects_are_rate_limited_per_hour(lab):
    lab.w.app.state.virga_limiter = RateLimiter({"virga": (2, 100)}, clock=lambda: 0.0)
    c, h = operator(lab.w)
    for _ in range(2):
        assert c.post("/api/virga/trials", json={"scenario": "CAM-06"}, headers=h).status_code == 422
    r = c.post("/api/virga/trials", json={"scenario": "CAM-06"}, headers=h)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 60
    assert _rows(lab.w, "virga_inject")[0]["status"] == "refused:rate_limited"


def test_off_unless_enabled(world, tmp_path):  # noqa: F811
    world.app.state.virga_live = vl.VirgaLive(enabled=False, store=tmp_path / "s", publish=tmp_path / "p",
                                              catalogue=fake_cat(), ports=lambda: {}, holders=lambda: [])
    c, h = operator(world)
    st = c.get("/api/virga/state").json()
    assert st["enabled"] is False and "SHIMPAKU_VIRGA_LIVE" in st["reason"]
    assert c.post("/api/virga/trials", json={"scenario": "FAB-01"}, headers=h).status_code == 403


def test_settings_gate_is_off_by_default():
    from shimpaku.config import Settings
    assert Settings().virga_live is False and Settings(virga_live="1").virga_live is True


# ================================================================ replay and the canned demo

@pytest.fixture
def demo_client():
    app = create_app(demo, auth_mode="off")
    clock = SimpleNamespace(t=time.time())
    app.state.virga_live = DemoVirga(clock=lambda: clock.t)
    c = TestClient(app)
    return c, csrf(c), clock


def test_replay_never_injects_and_shows_recorded_history(tmp_path, demo_client):
    from shimpaku.snapshot_cli import collect
    src, h, _ = demo_client

    def get(path, **params):
        if path == "/api/audit/verify":
            return {"ok": True, "rows": 4}
        return src.get(path, params=params).json()

    p = tmp_path / "snap.json"
    p.write_text(json.dumps(collect(get), default=str))
    replay.load(p)
    c = TestClient(create_app(replay, auth_mode="off"))
    st = c.get("/api/virga/state").json()
    assert st == {**st, "enabled": False, "reason": vl.REPLAY_REASON, "busy": False, "trial": None}
    assert st["recent"] and st["recent"][0]["scenario"] == "FAB-01"
    cat = c.get("/api/virga/catalogue").json()
    assert cat["enabled"] is False and cat["scenarios"] and not any(s["demo_ok"] for s in cat["scenarios"])
    assert c.get("/api/virga/trials").json()["trials"]
    hh = csrf(c)
    for r in _attempts(c, hh):
        assert r == 403
    assert c.post("/api/virga/trials", json={"scenario": "FAB-01"}, headers=hh).json()["detail"] == vl.REPLAY_REASON
    assert c.get("/api/public/health").json() == {"campus": "offline", "fabric": "offline", "drill": "idle",
                                                  "lab": "replay"}


def test_canned_demo_walks_a_simulated_trial(demo_client):
    c, h, clock = demo_client
    cat = c.get("/api/virga/catalogue").json()
    assert cat["simulated"] is True and {s["id"] for s in cat["scenarios"] if s["demo_ok"]} >= {"FAB-01", "CAM-07"}
    assert not next(s for s in cat["scenarios"] if s["id"] == "CAM-06")["demo_ok"]
    tid = c.post("/api/virga/trials", json={"scenario": "FAB-01", "hold_s": 180}, headers=h).json()["trial_id"]
    st = c.get("/api/virga/state").json()
    assert st["simulated"] and st["busy"] and st["trial"]["phase"] == "preflight"
    assert c.get("/api/public/summary").json()["drill"] == {"active": True, "scenario": "FAB-01", "phase": "preflight",
                                                           "title": st["trial"]["title"], "since": st["trial"]["t_start"],
                                                           "site": "fabric"}
    clock.t += 20
    st = c.get("/api/virga/state").json()
    assert st["trial"]["phase"] == "hold" and st["trial"]["hold_until"]
    assert c.post(f"/api/virga/trials/{tid}/revert", headers=h).status_code == 202
    clock.t += 13
    st = c.get("/api/virga/state").json()
    assert st["trial"]["phase"] == "done" and st["trial"]["verdict"] == "pass" and not st["busy"]
    assert [p["status"] for p in st["trial"]["phases"]] == ["ok"] * 6
    assert "operator" in st["trial"]["phases"][3]["detail"]
    assert st["recent"][0]["id"] == tid
    assert c.get("/api/public/summary").json()["drill"]["active"] is False


# ================================================================ public health

def test_public_health_shape_on_the_demo(demo_client):
    c, _, _ = demo_client
    r = c.get("/api/public/health")
    assert r.status_code == 200 and set(r.json()) == {"campus", "fabric", "drill", "lab"}
    body = r.json()
    assert body["campus"] in ("healthy", "degraded", "offline") and body["fabric"] in ("healthy", "degraded", "offline")
    assert body["drill"] == "idle" and body["lab"] == "live" and "max-age=10" in r.headers["cache-control"]


def test_site_health_rules():
    inv = {"nodes": [{"name": "r1", "site": "campus"}, {"name": "sw1", "site": "campus"}, {"name": "leaf1", "site": "fabric"}]}
    ok = {"devices": [{"device": n, "sampled_at": 1.0} for n in ("r1", "sw1", "leaf1")]}
    sle_good = {"sles": [{"goal_pct": 99, "series": [{"t": 1, "value": 50}, {"t": 2, "value": 100}]}]}
    sle_bad = {"sles": [{"goal_pct": None, "series": [{"t": 1, "value": 100}, {"t": 2, "value": 97.5}]}]}
    assert site_health(sle_good, inv, ok, "campus") == "healthy"
    # one SLE minute below goal (e.g. a CPU spike on the lab host) is not enough on its own
    assert site_health(sle_bad, inv, ok, "campus") == "healthy"
    incs = {"incidents": [{"site": "campus", "status": "open"}, {"site": "fabric", "status": "closed"}]}
    assert site_health(sle_bad, inv, ok, "campus", incs) == "degraded"
    assert site_health(sle_good, inv, ok, "fabric", incs) == "healthy"
    drill = {"active": True, "site": "fabric", "phase": "hold"}
    assert site_health(sle_good, inv, ok, "fabric", None, drill) == "degraded"
    assert site_health(sle_good, inv, ok, "fabric", None, {**drill, "phase": "inject"}) == "healthy"  # not verified yet
    assert site_health(sle_good, inv, ok, "campus", None, drill) == "healthy"
    one_down = {"devices": [{"device": "r1", "sampled_at": None}, {"device": "sw1", "sampled_at": 1.0}]}
    assert site_health(sle_good, inv, one_down, "campus") == "degraded"
    assert site_health(sle_good, inv, {"devices": []}, "campus") == "offline"  # every device failing
    assert site_health({"available": False}, inv, {"available": False}, "fabric") == "offline"


def test_stream_sends_the_state(demo_client):
    c, _, _ = demo_client
    with c.stream("GET", "/api/virga/stream") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        lines = r.iter_lines()
        for line in lines:
            if line.startswith("event:"):
                assert line.split(":", 1)[1].strip() == "state"
                data = json.loads(next(lines).split(":", 1)[1])
                assert data["simulated"] is True and "recent" in data
                break


# ================================================================ abuse: stream fan-out, audit text

def _visitor_app(lab):
    from shimpaku import service
    app = create_app(service, auth_mode="local", web_dist=lab.w.tmp / "dist", leases=lab.w.lm,
                     limiter=RateLimiter({}), public_view=True, totp=lab.w.totp)
    app.state.virga_live = lab.m
    return app


def _first_state(lines):
    for line in lines:  # the caller keeps `lines`: dropping the iterator would close the stream
        if line.startswith("data:"):
            return json.loads(line.split(":", 1)[1])
    raise AssertionError("no state event")


def test_anonymous_streams_are_capped_per_address(lab, monkeypatch):
    from shimpaku.api import routes_virga
    monkeypatch.setattr(routes_virga, "STREAMS_PER_IP", 1)
    c = TestClient(_visitor_app(lab))
    with c.stream("GET", "/api/virga/stream") as r1:
        lines = r1.iter_lines()
        assert r1.status_code == 200 and "busy" in _first_state(lines)
        with c.stream("GET", "/api/virga/stream") as r2:
            assert r2.status_code == 429 and r2.headers["retry-after"] == "60"
    # the slot comes back when the viewer leaves
    _until(lambda: c.get("/api/virga/state").status_code == 200 and _stream_status(c) == 200, s=10)


def _stream_status(c):
    with c.stream("GET", "/api/virga/stream") as r:
        return r.status_code


def test_streams_have_a_global_cap(lab, monkeypatch):
    from shimpaku.api import routes_virga
    monkeypatch.setattr(routes_virga, "STREAMS_TOTAL", 1)
    c = TestClient(_visitor_app(lab))
    with c.stream("GET", "/api/virga/stream") as r1:
        assert r1.status_code == 200
        assert _stream_status(c) == 429
    monkeypatch.setattr(routes_virga, "STREAMS_TOTAL", 0)
    op, _ = operator(lab.w)
    assert _stream_status(op) == 200  # the operator's own view is never crowded out

