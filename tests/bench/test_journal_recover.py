import json

import pytest

from virga.catalogue import load
from virga.journal import Journal
from virga.probes import PROBES
from virga.recover import recover
from virga.runner import Context, execute

BASE = ["set system host-name leaf1", "set interfaces ge-0/0/0 description leaf1-spine1",
        "set protocols ospf area 0.0.0.0 interface ge-0/0/0.0 interface-type p2p"]


def _ctx(lab, tmp_path, cat=None, **kw):
    cat = cat or load_fake_catalogue()
    return Context(lab=lab, cat=cat, journal=Journal(tmp_path / "store" / "journal.json"), **kw)


def load_fake_catalogue():
    from types import SimpleNamespace

    lab = {"nodes": {"leaf1": {"mgmt": "x"}}, "allocation": {"write": ["leaf1"]}, "vars": {},
           "timing_defaults": {"preflight_timeout_s": 1, "inject_timeout_s": 1, "revert_timeout_s": 1, "poll_s": 0.01,
                               "fast_poll_s": 0.01, "stable": 2, "inject_ttl_min": 5, "detect_budget_s": 1,
                               "cooldown_s": 0},
           "health_sets": {}}
    return SimpleNamespace(lab=lab, scenarios={}, sha256="t", holdout_ids=set())


@pytest.fixture(autouse=True)
def fake_probe():
    PROBES["has_line"] = lambda lab, node, line: "present" if line in lab.cfg[node] else "absent"
    yield
    PROBES.pop("has_line", None)


def scenario(**over):
    sc = {"id": "TST-01", "title": "t", "layer": "fabric-underlay", "status": "executable", "executable": True,
          "requires": [], "probes": [{"id": "mtu", "kind": "has_line",
                                      "args": {"node": "leaf1", "line": "set interfaces ge-0/0/0 unit 0 family inet mtu 1400"},
                                      "healthy": "absent", "faulted": "present", "ttd": True}],
          "inject": [{"junos": {"node": "leaf1", "lines": ["set interfaces ge-0/0/0 unit 0 family inet mtu 1400"]}}],
          "revert": [{"junos": {"node": "leaf1", "lines": ["delete interfaces ge-0/0/0 unit 0 family inet mtu"]}}],
          "ground_truth": {"fault_class": "mtu-mismatch", "target_entity": "node:leaf1", "root_cause": {}, "blast_radius": {}}}
    sc.update(over)
    return sc


def test_selftest_trial_passes_and_clears_journal(fake_lab, tmp_path):
    lab = fake_lab({"leaf1": BASE})
    ctx = _ctx(lab, tmp_path)
    rec = execute(ctx, scenario())
    assert rec["verdict"] == "pass", json.dumps(rec["phases"], default=str)[:800]
    assert rec["phases"]["verify_reverted"]["config_restored"]
    assert lab.cfg["leaf1"] == BASE
    assert ctx.journal.entries() == {}
    inj = lab.commits[0]
    assert inj["confirm_min"] == 5 and not inj["confirm_after"]       # dead man's switch on the inject
    rev = lab.commits[1]
    assert rev["confirm_min"] == 2 and rev["confirm_after"]           # revert confirmed on a fresh session


def test_plain_inject_mode_uses_plain_commit(fake_lab, tmp_path):
    lab = fake_lab({"leaf1": BASE})
    ctx = _ctx(lab, tmp_path, inject_mode="plain")
    rec = execute(ctx, scenario())
    assert rec["verdict"] == "pass" and rec["inject_mode"] == "plain"
    assert lab.commits[0]["confirm_min"] is None


def test_unproven_fault_fails_but_is_still_reverted(fake_lab, tmp_path):
    lab = fake_lab({"leaf1": BASE})
    sc = scenario()
    sc["probes"][0]["faulted"] = "never-true"
    rec = execute(_ctx(lab, tmp_path), sc)
    assert rec["verdict"] == "fail"
    assert not rec["phases"]["verify_injected"]["ok"]
    assert rec["phases"]["verify_reverted"]["config_restored"] and lab.cfg["leaf1"] == BASE


def test_residue_keeps_journal_entry(fake_lab, tmp_path):
    lab = fake_lab({"leaf1": BASE})
    sc = scenario(revert=[{"junos": {"node": "leaf1", "lines": ["set interfaces ge-0/0/9 description oops"]}}])
    ctx = _ctx(lab, tmp_path)
    rec = execute(ctx, sc)
    assert rec["verdict"] == "fail"
    assert rec["phases"]["verify_reverted"]["config_diff"]["leaf1"]
    assert list(ctx.journal.entries())  # kept for `virga recover`


def test_blocked_on_unhealthy_baseline_never_injects(fake_lab, tmp_path):
    lab = fake_lab({"leaf1": BASE + ["set interfaces ge-0/0/0 unit 0 family inet mtu 1400"]})
    rec = execute(_ctx(lab, tmp_path), scenario())
    assert rec["verdict"].startswith("blocked")
    assert lab.commits == []


def test_blocked_on_pending_commit(fake_lab, tmp_path):
    lab = fake_lab({"leaf1": BASE})
    lab.pending_flag["leaf1"] = True
    rec = execute(_ctx(lab, tmp_path), scenario())
    assert rec["verdict"] == "blocked: commit pending" and lab.commits == []


def test_expected_rejection_is_a_pass_with_unchanged_config(fake_lab, tmp_path):
    lab = fake_lab({"leaf1": BASE})
    sc = scenario(expect_inject_rejected=True, probes=[], revert=[],
                  inject=[{"junos": {"node": "leaf1", "lines": ["set protocols ospf hello-interval banana"]}}])
    rec = execute(_ctx(lab, tmp_path), sc)
    assert rec["verdict"] == "pass" and rec["phases"]["verify_injected"]["config_unchanged"]


def test_write_outside_lease_is_refused(fake_lab):
    from virga.lab import NotLeased

    lab = fake_lab({"leaf1": BASE})
    with pytest.raises(NotLeased):
        lab.commit("leaf1", ["set x"], None)


def test_crash_after_inject_then_recover(fake_lab, tmp_path):
    """Simulate a runner that died after the inject commit: config has the fault, journal has the entry."""
    lab = fake_lab({"leaf1": BASE})
    j = Journal(tmp_path / "store" / "journal.json")
    j.put("TST-01-crash", {"scenario": "TST-01", "inject_mode": "plain", "nodes": ["leaf1"], "write_nodes": ["leaf1"],
                           "revert": scenario()["revert"], "snapshot": {"leaf1": BASE}, "ttl_min": 5,
                           "decoys": [{"decoy_id": "d0001", "node": "leaf1",
                                       "revert": ["delete policy-options prefix-list pl-spare12"]}]})
    lab.cfg["leaf1"] += ["set interfaces ge-0/0/0 unit 0 family inet mtu 1400",
                         "set policy-options prefix-list pl-spare12 192.0.2.12/32"]
    ctx = Context(lab=lab, cat=load_fake_catalogue(), journal=j)
    assert recover(ctx, j, pending_wait_s=0) == 0
    assert lab.cfg["leaf1"] == BASE
    assert j.entries() == {}


def test_recover_reports_residue_and_keeps_entry(fake_lab, tmp_path):
    lab = fake_lab({"leaf1": BASE})
    j = Journal(tmp_path / "store" / "journal.json")
    j.put("k", {"scenario": "TST-01", "nodes": ["leaf1"], "write_nodes": ["leaf1"], "revert": scenario()["revert"],
                "snapshot": {"leaf1": BASE}, "ttl_min": 5, "decoys": []})
    lab.cfg["leaf1"].append("set something-else-changed")
    ctx = Context(lab=lab, cat=load_fake_catalogue(), journal=j)
    assert recover(ctx, j, pending_wait_s=0) == 1
    assert "k" in j.entries()


def test_recover_waits_out_a_pending_confirm(fake_lab, tmp_path):
    """A confirmed inject must not be 'reverted' by a commit while pending: that would confirm the fault."""
    lab = fake_lab({"leaf1": BASE})
    lab.pending_flag["leaf1"] = True
    j = Journal(tmp_path / "store" / "journal.json")
    j.put("k", {"scenario": "TST-01", "nodes": ["leaf1"], "write_nodes": ["leaf1"], "revert": scenario()["revert"],
                "snapshot": {"leaf1": BASE}, "ttl_min": 5, "decoys": []})
    ctx = Context(lab=lab, cat=load_fake_catalogue(), journal=j)
    assert recover(ctx, j, pending_wait_s=0) == 1
    assert lab.commits == [] and "k" in j.entries()


def test_journal_is_atomic_and_private(tmp_path):
    j = Journal(tmp_path / "journal.json")
    j.put("a", {"x": 1})
    j.append("a", "decoys", {"decoy_id": "d1"})
    j.pop_item("a", "decoys", {"decoy_id": "d1"})
    assert j.entries() == {"a": {"x": 1, "decoys": []}}
    assert (tmp_path / "journal.json").stat().st_mode & 0o077 == 0
    assert not (tmp_path / "journal.tmp").exists()


def test_repo_catalogue_selftest_with_fake_lab_respects_allocation(fake_lab, tmp_path):
    from conftest import ROOT

    cat = load(ROOT / "bench" / "catalogue.yaml")
    lab = fake_lab({"leaf1": BASE, "spine1": [], "r1": [], "leaf2": []}, write_ok={"leaf1", "spine1", "r1"})
    from virga.lab import NotLeased

    with pytest.raises((PermissionError, NotLeased)):
        with lab.leased(["leaf2"]):
            lab.commit("leaf2", ["set x"], None)
    assert cat.lab["allocation"]["write"] == ["leaf1", "spine1", "r1"]
