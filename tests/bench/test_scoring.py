"""Scorer on RECORDED trials: the R9 live self-test records (2026-10-23, tests/bench/data)."""

import copy
import json
import math

from conftest import DATA, ROOT
from virga.catalogue import load
from virga.publish import _clean, scoreboard_svg, selftest_summary
from virga.scoring import class_stats, fmt_table, records_to_trials, run_meta, score_system, wilson


def recorded():
    cat = load(ROOT / "bench" / "catalogue.yaml")
    recs = []
    for f in sorted(DATA.glob("selftest-*.jsonl")):
        for ln in f.read_text().splitlines():
            r = json.loads(ln)
            r["ground_truth"] = cat.scenarios[r["scenario"]]["ground_truth"]  # pre-R5-field records
            r["expect_inject_rejected"] = bool(cat.scenarios[r["scenario"]].get("expect_inject_rejected"))
            recs.append(r)
    return cat, recs


def oracle(trials, delay=5.0):
    return [{"incident_id": f"O{i}", "t_detect": t["t_effective"] + delay,
             "root_causes": [{"entity": t["target_entity"], "fault_class": t["fault_class"]}],
             "blast_radius": t["impacted_entities"]} for i, t in enumerate(trials) if t["fault_class"] != "none"]


def respace(trials, gap=900.0):
    out, t0 = [], trials[0]["t_inject"]
    for k, t in enumerate(sorted(trials, key=lambda x: x["t_inject"])):
        t = copy.deepcopy(t)
        shift = t0 + k * gap - t["t_inject"]
        for f in ("t_inject", "t_effective", "t_cleared"):
            t[f] += shift
        out.append(t)
    return out


def test_records_to_trials_voids_unproven_and_rejection():
    _, recs = recorded()
    trials = records_to_trials(recs, "R-test")
    scen = [t["scenario"] for t in trials]
    assert "CFG-02" not in scen                      # a validation check, not a fault
    assert len(trials) == 9                          # 11 records: one FAB-04 failed (void), one CFG-02
    for t in trials:
        assert t["t_effective"] >= t["t_inject"]
        assert t["t_cleared"] > t["t_effective"]
        assert t["target_entity"] in t["entity_aliases"]


def test_null_system_scores_zero():
    _, recs = recorded()
    trials = records_to_trials(recs, "R-test")
    s, _ = score_system(trials, [], run_meta(trials, "R-test"), "null")
    assert s["detection"] == 0 and s["fp"] == 0 and math.isinf(s["ttd_median_s"])


def test_packed_records_reproduce_the_merge_artefact_and_respacing_fixes_it():
    """R9 §0: packed ~10 s apart, the matcher merges incidents; re-spaced 900 s the oracle is perfect."""
    _, recs = recorded()
    trials = records_to_trials(recs, "R-test")
    s_packed, _ = score_system(trials, oracle(trials), run_meta(trials, "R-test"), "oracle")
    assert s_packed["detection"] < 1.0
    spaced = respace(trials)
    s, P = score_system(spaced, oracle(spaced), run_meta(spaced, "R-test"), "oracle")
    assert s["detection"] == 1.0 and s["rca_top1"] == 1.0 and s["br_f1"] == 1.0 and s["fp"] == 0
    assert s["ttd_median_s"] == 5.0
    assert "| oracle | 9 |" in fmt_table([s])


def test_alias_matches_either_end_of_a_link():
    _, recs = recorded()
    spaced = respace(records_to_trials(recs, "R"))
    inc = []
    for i, t in enumerate(spaced):
        ent = t["entity_aliases"][t["target_entity"]][0] if t["entity_aliases"][t["target_entity"]] else t["target_entity"]
        inc.append({"incident_id": f"A{i}", "t_detect": t["t_effective"] + 1,
                    "root_causes": [{"entity": ent, "fault_class": t["fault_class"]}], "blast_radius": []})
    s, _ = score_system(spaced, inc, run_meta(spaced, "R"), "alias")
    assert s["rca_top1"] == 1.0


def test_false_positive_in_clean_time_counts():
    _, recs = recorded()
    spaced = respace(records_to_trials(recs, "R"))
    fp = {"incident_id": "FP", "t_detect": spaced[-1]["t_cleared"] + 5000, "root_causes": [], "blast_radius": []}
    s, _ = score_system(spaced, oracle(spaced) + [fp], run_meta(spaced, "R"), "o")
    assert s["fp"] == 1 and s["precision"] < 1


def test_null_trials_from_control_windows():
    recs = [{"kind": "control", "scenario": "CTRL-00", "t_start": "2026-10-24T00:00:00Z", "t_end": "2026-10-24T00:05:00Z"}]
    t = records_to_trials(recs, "R")
    assert t[0]["fault_class"] == "none" and t[0]["t_cleared"] - t[0]["t_inject"] == 300


def test_class_stats_cluster_bootstrap():
    _, recs = recorded()
    spaced = respace(records_to_trials(recs, "R"))
    inc = oracle(spaced)[::2]  # detect every other trial
    s, P = score_system(spaced, inc, run_meta(spaced, "R"), "half")
    cs = class_stats(P, n_boot=300)
    classes = {r["fault_class"] for r in cs["per_class"]}
    assert "link-down" in classes and "mtu-mismatch" in classes
    lo, hi = cs["cluster_ci"]["detection"]
    assert 0 <= lo <= s["detection"] <= hi <= 1
    assert cs["cluster_ci"]["classes"] == len(classes)


def test_wilson_matches_r5():
    lo, hi = wilson(8, 10)
    assert round(lo, 3) == 0.490 and round(hi, 3) == 0.943


def test_publish_outputs_are_json_clean(tmp_path):
    cat, recs = recorded()
    spaced = respace(records_to_trials(recs, "R"))
    S, PER = [], []
    for name, inc in (("null", []), ("oracle", oracle(spaced))):
        s, P = score_system(spaced, inc, run_meta(spaced, "R"), name)
        S.append(s)
        PER.append(P)
    txt = json.dumps(_clean({"systems": S}), allow_nan=False)
    assert '"ttd_median_s": null' in txt
    svg = scoreboard_svg(S, PER, tmp_path / "s.svg").read_text()
    assert svg.startswith("<svg") and "oracle 100%" in svg
    summ = selftest_summary(recs, cat, ["x"])
    assert summ["scenarios"]["FAB-04"]["fails"] == 1 and summ["scenarios"]["FAB-04"]["passes"] == 1
    assert summ["scenarios"]["FAB-07"]["passes"] == 1
