"""Runner demo mode (hold, revert now, the D5 TTL rule, progress events) and the daemon's Virga live
manager over the real runner with a FakeLab. No lab."""

from __future__ import annotations

import json
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_journal_recover import BASE, _ctx, fake_probe, load_fake_catalogue, scenario  # noqa: E402,F401

from virga.catalogue import load  # noqa: E402
from virga.journal import Journal  # noqa: E402
from virga.runner import demo_ttl_min, execute  # noqa: E402

from shimpaku import virga_live as vl  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def _later(s: float, fn) -> threading.Thread:
    t = threading.Timer(s, fn)
    t.start()
    return t


# ================================================================ runner: demo mode

def test_demo_hold_ends_early_on_revert_now_and_inject_is_confirmed_for_the_hold(fake_lab, tmp_path):
    lab = fake_lab({"leaf1": BASE})
    events: list[dict] = []
    ctx = _ctx(lab, tmp_path, mode="demo", hold_s=600, on_event=events.append)
    seen_ttl = {}

    def on(ev):
        events.append(ev)
        if ev.get("type") == "phase" and ev["name"] == "hold" and ev["status"] == "running":
            seen_ttl["journal"] = next(iter(ctx.journal.entries().values()))["ttl_min"]
            _later(0.2, ctx.revert_now.set)
    ctx.on_event = on
    t0 = time.time()
    rec = execute(ctx, scenario())
    assert rec["verdict"] == "pass", json.dumps(rec["phases"], default=str)[:800]
    h = rec["phases"]["hold"]
    assert h["ended_by"] == "operator" and h["elapsed_s"] < 5 and time.time() - t0 < 10
    assert h["samples"] >= 1 and h["fault_present_samples"] == h["samples"] and h["t_fix_verified"] is None
    assert demo_ttl_min(600) == 13
    # FAB-09 can take 240 s to show its fault; a 600 s hold must not outlive the device's own timer
    assert demo_ttl_min(600, 240) == 17
    inj = lab.commits[0]
    # D5: the timer covers the time to show the fault (inject_timeout_s is 1 s in this test catalogue) plus the hold, + 3 min
    assert inj["confirm_min"] == demo_ttl_min(600, 1) == 14 and not inj["confirm_after"]
    assert seen_ttl["journal"] == 13                                       # `virga recover` waits long enough
    assert lab.cfg["leaf1"] == BASE and ctx.journal.entries() == {}
    phases = [(e["name"], e["status"]) for e in events if e["type"] == "phase"]
    assert phases == [("preflight", "running"), ("preflight", "ok"), ("inject", "running"), ("inject", "ok"),
                      ("verify_injected", "running"), ("verify_injected", "ok"), ("hold", "running"), ("hold", "ok"),
                      ("revert", "running"), ("revert", "ok"), ("verify_reverted", "running"),
                      ("verify_reverted", "ok")]
    assert next(e for e in events if e["type"] == "phase" and e["name"] == "hold")["hold_until"] > t0
    assert events[-1] == {**events[-1], "type": "done", "verdict": "pass"}
    assert any(e["type"] == "log" and "verdict PASS" in e["msg"] for e in events)


def test_demo_hold_times_out_and_the_ttl_floor_is_the_catalogue_ttl(fake_lab, tmp_path):
    lab = fake_lab({"leaf1": BASE})
    rec = execute(_ctx(lab, tmp_path, mode="demo", hold_s=0.3), scenario())
    assert rec["verdict"] == "pass" and rec["phases"]["hold"]["ended_by"] == "timeout"
    assert lab.commits[0]["confirm_min"] == 5  # max(catalogue 5, ceil(0.3/60)+3 = 4)


def test_demo_raises_an_explicit_short_ttl_and_never_injects_plain(fake_lab, tmp_path):
    lab = fake_lab({"leaf1": BASE})
    sc = scenario()
    sc["inject"][0]["junos"]["ttl_min"] = 1
    ctx = _ctx(lab, tmp_path, mode="demo", hold_s=240, inject_mode="plain")
    ctx.revert_now.set()  # pressed before the hold began: the hold ends at once
    rec = execute(ctx, sc)
    assert rec["inject_mode"] == "confirmed" and lab.commits[0]["confirm_min"] == demo_ttl_min(240, 1) == 8
    assert rec["phases"]["hold"]["ended_by"] == "operator" and rec["phases"]["hold"]["samples"] == 0


def test_demo_hold_records_a_fix_made_by_someone_else(fake_lab, tmp_path):
    lab = fake_lab({"leaf1": BASE})
    ctx = _ctx(lab, tmp_path, mode="demo", hold_s=3)

    def on(ev):
        if ev.get("type") == "phase" and ev["name"] == "hold" and ev["status"] == "running":
            lab.cfg["leaf1"] = list(BASE)  # an out-of-band fix while the fault is held
    ctx.on_event = on
    rec = execute(ctx, scenario())
    h = rec["phases"]["hold"]
    assert h["t_fix_verified"] is not None and h["fault_present_at_end"] is False
    assert rec["verdict"] == "pass" and lab.cfg["leaf1"] == BASE


def test_selftest_mode_has_no_hold_and_a_raising_callback_breaks_nothing(fake_lab, tmp_path):
    lab = fake_lab({"leaf1": BASE})

    def boom(ev):
        raise RuntimeError("viewer went away")
    rec = execute(_ctx(lab, tmp_path, on_event=boom), scenario())
    assert rec["verdict"] == "pass" and "hold" not in rec["phases"] and lab.commits[0]["confirm_min"] == 5


def test_blocked_preflight_marks_every_later_phase_skipped(fake_lab, tmp_path):
    lab = fake_lab({"leaf1": BASE + ["set interfaces ge-0/0/0 unit 0 family inet mtu 1400"]})
    events: list[dict] = []
    rec = execute(_ctx(lab, tmp_path, mode="demo", hold_s=60, on_event=events.append), scenario())
    assert rec["verdict"].startswith("blocked") and lab.commits == []
    st = {e["name"]: e["status"] for e in events if e["type"] == "phase"}
    assert st == {"preflight": "failed", "inject": "skipped", "verify_injected": "skipped", "hold": "skipped",
                  "revert": "skipped", "verify_reverted": "skipped"}


def test_residue_config_lines_never_reach_the_callback(fake_lab, tmp_path):
    lab = fake_lab({"leaf1": BASE})
    events: list[dict] = []
    sc = scenario(revert=[{"junos": {"node": "leaf1", "lines": ["set interfaces ge-0/0/9 description oops"]}}])
    rec = execute(_ctx(lab, tmp_path, mode="demo", hold_s=0.1, on_event=events.append), sc)
    assert rec["verdict"] == "fail" and rec["phases"]["verify_reverted"]["config_diff"]
    blob = json.dumps(events)
    assert "oops" not in blob and "RESIDUE on leaf1" in blob


# ================================================================ the manager over the real runner

def _cat(**over):
    sc = scenario(status="selftested", requires=["day-06-fabric"], **over)
    return SimpleNamespace(lab=load_fake_catalogue().lab, scenarios={"TST-01": sc}, sha256="t", holdout_ids=set())


@pytest.fixture
def audit_log(tmp_path):
    from shimpaku import service
    from shimpaku.mcp.audit import AuditLog
    a = AuditLog(tmp_path / "audit.db", tmp_path / "audit.jsonl")
    service.set_audit_log(a)
    yield a
    service.set_audit_log(None)


def _manager(tmp_path, lab, cat=None, **kw):
    return vl.VirgaLive(enabled=True, store=tmp_path / "store", publish=tmp_path / "pub", catalogue=cat or _cat(),
                        lab_factory=lambda c, holder: lab, ports=lambda: {("leaf1", "ge-0/0/0"): "spine1"},
                        holders=lambda: [], **kw)


def _wait(m, cond, s=10.0):
    t = time.time() + s
    while time.time() < t:
        if cond(m.state()):
            return m.state()
        time.sleep(0.02)
    raise AssertionError(f"timed out; state {json.dumps(m.state(), default=str)[:600]}")


OP = {"kind": "dashboard", "name": "admin", "role": "operator", "transport": "rest", "lease_id": None}


def test_manager_runs_one_trial_streams_state_and_keeps_private_and_public_records(fake_lab, tmp_path, audit_log):
    lab = fake_lab({"leaf1": BASE})
    m = _manager(tmp_path, lab)
    tid = m.start("TST-01", 600, OP)
    st = _wait(m, lambda s: (s["trial"] or {}).get("phase") == "hold")
    assert st["busy"] and not st["needs_recovery"] and st["trial"]["id"] == tid and st["trial"]["hold_until"]
    assert st["trial"]["links"] == [["leaf1", "spine1"]] and st["trial"]["site"] == "fabric"
    assert m.drill()["active"] and m.drill()["phase"] == "hold"
    with pytest.raises(vl.Refused) as e:
        m.start("TST-01", None, OP)
    assert e.value.status == 409 and e.value.code == "busy"
    m.revert(tid, OP)
    st = _wait(m, lambda s: not s["busy"])
    t = st["trial"]
    assert t["phase"] == "done" and t["verdict"] == "pass" and [p["status"] for p in t["phases"]] == ["ok"] * 6
    assert st["recent"][0]["id"] == tid and st["recent"][0]["verdict"] == "pass"
    assert st["recent"][0]["time_to_effect_s"] is not None and st["recent"][0]["incident_opened"] is False
    # allow-list: no config line, no address, the actor is a role
    blob = json.dumps(st)
    assert "set interfaces" not in blob and "mtu 1400" not in blob and not IPV4.search(blob) and '"admin"' not in blob
    assert t["by_role"] == "operator"
    # the private record holds the full runner record; the public history survives a restart
    priv = list((tmp_path / "store" / "live").glob("live-*.jsonl"))
    assert len(priv) == 1 and "mtu 1400" in priv[0].read_text()
    assert (tmp_path / "store" / "live").stat().st_mode & 0o077 == 0
    assert _manager(tmp_path, lab).state()["recent"][0]["id"] == tid
    rows = {r["tool"]: r for r in audit_log.query(limit=50)}
    assert rows["virga_inject"]["status"] == "changed" and rows["virga_inject"]["args"]["nodes"] == ["leaf1"]
    assert rows["virga_inject"]["actor"]["role"] == "operator" and rows["virga_revert"]["status"] == "ok"
    assert audit_log.verify()["ok"]


def test_a_failed_revert_needs_recovery_and_recover_clears_it(fake_lab, tmp_path, audit_log):
    lab = fake_lab({"leaf1": BASE})
    m = _manager(tmp_path, lab)
    tid = m.start("TST-01", 30, OP)
    _wait(m, lambda s: (s["trial"] or {}).get("phase") == "hold")
    lab.fail_commit_on = {"leaf1"}  # the revert commit is refused: the fault stays, the journal keeps it
    m.revert(tid, OP)
    st = _wait(m, lambda s: not s["busy"])
    assert st["trial"]["verdict"] == "fail" and st["needs_recovery"]
    assert {p["name"]: p["status"] for p in st["trial"]["phases"]}["revert"] == "failed"
    with pytest.raises(vl.Refused) as e:
        m.start("TST-01", None, OP)
    assert e.value.status == 409 and e.value.code == "needs_recovery"
    lab.fail_commit_on = set()
    lab.pending_flag["leaf1"] = False  # the commit-confirmed window has lapsed
    out = m.recover(OP)
    assert out == {"recovered": [tid], "ok": True} and not m.state()["needs_recovery"] and lab.cfg["leaf1"] == BASE
    assert audit_log.query(tool="virga_recover")[0]["status"] == "ok"


def test_journal_left_by_a_crashed_cli_is_needs_recovery_at_start(fake_lab, tmp_path):
    Journal(tmp_path / "store" / "journal.json").put("TST-01-crash", {"scenario": "TST-01", "nodes": ["leaf1"]})
    m = _manager(tmp_path, fake_lab({"leaf1": BASE}))
    assert m.state()["needs_recovery"] is True


def test_lease_held_by_another_writer_is_409(fake_lab, tmp_path):
    m = _manager(tmp_path, fake_lab({"leaf1": BASE}))
    m.holders_fn = lambda: [{"device": "leaf1", "holder": "shimpaku-demo:tester:L1", "expires": time.time() + 60}]
    with pytest.raises(vl.Refused) as e:
        m.start("TST-01", None, OP)
    assert e.value.status == 409 and e.value.code == "lease_held" and "tester" not in e.value.message


def test_disabled_manager_refuses_and_still_shows_the_catalogue(tmp_path):
    m = vl.VirgaLive(enabled=False, store=tmp_path / "s", publish=tmp_path / "p", catalogue=_cat(),
                     ports=lambda: {}, holders=lambda: [])
    with pytest.raises(vl.Refused) as e:
        m.start("TST-01", None, OP)
    assert e.value.status == 403 and "SHIMPAKU_VIRGA_LIVE" in e.value.message
    c = m.catalogue_view()
    assert c["enabled"] is False and c["scenarios"][0]["id"] == "TST-01" and c["scenarios"][0]["demo_ok"]


def test_catalogue_view_of_the_repo_catalogue(tmp_path):
    cat = load(ROOT / "bench" / "catalogue.yaml")
    pub = tmp_path / "pub"
    pub.mkdir()
    (pub / "results.json").write_text(json.dumps({"selftest": {"generated": "2026-10-24T18:05:00+00:00", "scenarios": {
        "FAB-01": {"passes": 4, "trials": 5, "last": "2026-10-24T18:02:11.000Z", "time_to_effect_s_median": 0.5,
                   "time_to_restore_s_median": 9.0}}}}))
    from shimpaku.api.demo_virga import PORTS
    m = vl.VirgaLive(enabled=False, store=tmp_path / "s", publish=pub, catalogue=cat, ports=lambda: PORTS,
                     holders=lambda: [])
    rows = {r["id"]: r for r in m.catalogue_view()["scenarios"]}
    assert "CTRL-00" not in rows
    f1 = rows["FAB-01"]
    assert f1["site"] == "fabric" and f1["nodes"] == ["leaf1"] and f1["links"] == [["leaf1", "spine1"]]
    assert f1["demo_ok"] and f1["why_not"] is None and f1["impact"] == "user"
    assert f1["selftest"] == {"passes": 4, "runs": 5, "last_at": datetime(2026, 10, 24, 18, 2, 11, tzinfo=timezone.utc).timestamp()}
    assert rows["FAB-03"]["impact"] == "latent" and rows["CFG-02"]["impact"] == "latent"
    assert rows["FAB-03"]["links"] == [["leaf1", "spine1"]] and rows["FAB-03"]["demo_ok"]
    assert not rows["CAM-06"]["demo_ok"] and "management" in rows["CAM-06"]["why_not"]
    assert not rows["CAM-01"]["demo_ok"] and "aiops-farm" in rows["CAM-01"]["why_not"]
    assert rows["CAM-07"]["site"] == "campus" and rows["CAM-07"]["links"] == [["r1", "r2"]]
    assert all(isinstance(r["est_s"], int) and r["est_s"] > 0 for r in rows.values())
    assert set(f1) == {"id", "title", "layer", "site", "nodes", "links", "impact", "demo_ok", "why_not", "est_s",
                       "selftest"}


def test_public_line_removes_config_and_addresses():
    line = vl.public_line("    inject FAILED: RuntimeError: set interfaces ge-0/0/0 disable on 10.99.0.41 /home/lab/x")
    assert "set interfaces" not in line and "10.99" not in line and "/home/lab" not in line
    assert line.startswith("inject FAILED: RuntimeError: <config>")
