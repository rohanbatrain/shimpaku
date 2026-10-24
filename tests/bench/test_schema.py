import copy

import pytest
import yaml

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # not `from conftest import ROOT`: see test_journal_recover.py
from virga import schema
from virga.catalogue import CatalogueError, expand_probes, load, scenario_env

CAT = ROOT / "bench" / "catalogue.yaml"


def _docs():
    docs = [d for d in yaml.safe_load_all(CAT.read_text()) if d]
    return docs[0], {d["id"]: d for d in docs[1:]}


def test_repo_catalogue_is_valid():
    cat = load(CAT)
    assert len(cat.scenarios) == 37
    statuses = {s["status"] for s in cat.scenarios.values()}
    assert statuses <= schema.STATUSES
    # the 35 R9 scenarios plus link-flap and ospf-timer-mismatch
    assert {"FAB-11", "FAB-12"} <= set(cat.scenarios)
    assert cat.scenarios["FAB-11"]["ground_truth"]["fault_class"] == "link-flap"
    assert cat.scenarios["FAB-12"]["ground_truth"]["fault_class"] == "ospf-timer-mismatch"


def test_farm_scenarios_are_not_executable():
    cat = load(CAT)
    for s in cat.scenarios.values():
        if "aiops-farm" in s["requires"]:
            assert not s["executable"], s["id"]
    runnable = cat.runnable({"day-06-fabric", "day-02-campus"})
    assert set(runnable) >= {"FAB-01", "FAB-06", "FAB-09", "CAM-07", "WAN-03", "CFG-02"}
    assert "CFG-02" not in cat.benchable({"day-06-fabric", "day-02-campus"}, False)


@pytest.mark.parametrize("mutate, needle", [
    (lambda s: s["probes"].append({"id": "x", "kind": "nope", "faulted": 1}), "unknown kind"),
    (lambda s: s["inject"].append({"junos": {"node": "leaf2", "lines": ["set x"]}}), "outside allocation.write"),
    (lambda s: s["inject"].append({"junos": {"node": "leaf9", "lines": ["set x"]}}), "unknown node"),
    (lambda s: s["inject"][0]["junos"]["lines"].append("set ${nosuchvar}"), "unresolved variable"),
    (lambda s: s.update(health_override=[{"id": "fabric:does-not-exist", "faulted": 1}]), "matches no probe"),
    (lambda s: s.update(revert=[]), "without a revert"),
    (lambda s: s.update(status="designed"), "executable: true needs status"),
    (lambda s: s["ground_truth"].update(target_entity="leaf1 ge-0/0/0"), "canonical R5 id"),
    (lambda s: s.update(timing={"bogus_s": 1}), "unknown timing key"),
    (lambda s: s["probes"].append({"id": "y", "kind": "ospf", "faulted": {"approx": 1}}), "unknown expectation operator"),
    (lambda s: s.update(revert=[{"auto": {"node": "leaf1"}}]), "ttl_min <= 2"),
])
def test_validator_catches(mutate, needle):
    lab, scen = _docs()
    sc = copy.deepcopy(scen["FAB-01"])
    mutate(sc)
    errs = schema.validate_scenario(sc, lab)
    assert any(needle in e for e in errs), errs


def test_no_faulted_expectation_is_an_error():
    lab, scen = _docs()
    sc = copy.deepcopy(scen["FAB-07"])
    sc["health_override"] = []
    sc["probes"] = [{k: v for k, v in p.items() if k != "faulted"} for p in sc["probes"]]
    assert any("no faulted expectation" in e for e in schema.validate_scenario(sc, lab))


def test_duplicate_ids_and_load_error(tmp_path):
    txt = CAT.read_text()
    first = txt.split("\n---\n")
    p = tmp_path / "c.yaml"
    p.write_text("\n---\n".join(first + [first[1]]))
    with pytest.raises(CatalogueError, match="duplicate scenario id"):
        load(p)


def test_holdout_file_is_merged_and_flagged(tmp_path):
    lab, scen = _docs()
    h = copy.deepcopy(scen["FAB-07"])
    h["id"] = "HOL-01"
    hp = tmp_path / "holdout.yaml"
    hp.write_text(yaml.safe_dump(h))
    cat = load(CAT, hp)
    assert "HOL-01" in cat.holdout_ids
    assert "HOL-01" not in cat.benchable({"day-06-fabric", "day-02-campus"}, include_holdout=False)
    assert "HOL-01" in cat.benchable({"day-06-fabric", "day-02-campus"}, include_holdout=True)
    assert cat.sha256 != load(CAT).sha256


def test_overrides_and_intensity_substitution():
    cat = load(CAT)
    sc = cat.scenarios["FAB-04"]
    env = scenario_env(cat.lab, sc, {"mtu": 1300})
    probes = {p["id"]: p for p in expand_probes(cat.lab, sc, env)}
    assert probes["fabric:lf1-bfd-sp1"]["faulted"] == "Up"
    assert probes["fabric:lf1-bfd-sp1"]["healthy"] == "Up"
    assert env["mtu"] == "1300"
