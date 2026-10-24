"""Analytics tests on synthetic fixtures (no live lab)."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from shimpaku.analytics import blast, detectors, incidents, rules
from shimpaku.analytics.loop import run_once
from shimpaku.analytics.rpm import parse_probe_results
from shimpaku.analytics.sle import (APP, FABRIC, assert_classifier_sums, classify_fabric, fabric_minutes,
                                    probe_minutes, summarise)

from . import synth

F_START, F_END, MINUTES = 70, 80, 90


def drive(con, upto_min, start_min=62):
    """Call run_once once per minute like the daemon (3 s past each boundary)."""
    out = []
    for m in range(start_min, upto_min + 1):
        out.append(run_once(con, now=synth.T0 + pd.Timedelta(minutes=m, seconds=3)))
    return out


def incidents_of(con):
    return con.execute("SELECT incident_id, t_open, t_detect, t_close, sle, root_causes, status FROM incident").fetchall()


# ------------------------------------------------------------------ SLE accounting
def test_fabric_classifier_sums_and_n():
    s = synth.samples(MINUTES, "FAB-02", F_START, F_END)
    fab = fabric_minutes(s, synth.T0 + pd.Timedelta(minutes=60), synth.T0 + pd.Timedelta(minutes=MINUTES))
    assert_classifier_sums(fab)
    summ = summarise(fab, FABRIC)
    assert summ.n == 7 * 30
    assert sum(c["units"] for c in summ.classifiers) == summ.bad
    assert "(n = 210)" in summ.label()
    inside = fab[(fab.minute >= synth.T0 + pd.Timedelta(minutes=F_START)) & (fab.status == "bad")]
    by = dict(zip(inside.entity_id, inside.classifier))
    assert by["leaf1"] == "Adjacency" and by["spine1"] == "Redundancy"


def test_healthy_fabric_all_good():
    s = synth.samples(30)
    fab = fabric_minutes(s, synth.T0 + pd.Timedelta(minutes=10), synth.T0 + pd.Timedelta(minutes=30))
    assert (fab.status == "good").all() and len(fab) == 7 * 20


def test_precedence_one_classifier():
    ev = dict(ospf_expected=2, ospf_lost=[{"entity": "x", "state": "absent"}], links_down=["a:ge-0/0/0"],
              carrier_delta=[{"entity": "a:ge-0/0/0", "delta": 1}], cpu_pct=95)
    assert classify_fabric(ev) == ("Redundancy", "Link down")
    assert classify_fabric(dict(ospf_expected=2, ospf_lost=[{"entity": "x"}, {"entity": "y"}], links_down=["z"])) \
        == ("Adjacency", "All neighbours down")
    assert classify_fabric(dict(cpu_pct=95)) == ("Resources", "CPU")
    assert classify_fabric({}) == (None, None)
    assert classify_fabric(dict(ospf_expected=2, ospf_lost=[{"entity": "x", "state": "not-Full"}])) \
        == ("Adjacency", "Neighbour stuck")


def test_cpu_from_idle_and_high_cpu_is_resources():
    s = synth.samples(20)
    s.loc[(s.entity_id == "r2") & (s.metric == "cpu.idle") & (s.ts >= synth.T0 + pd.Timedelta(minutes=15)), "value"] = 5.0
    fab = fabric_minutes(s, synth.T0 + pd.Timedelta(minutes=10), synth.T0 + pd.Timedelta(minutes=20))
    bad = fab[fab.status == "bad"]
    assert set(bad.entity_id) == {"r2"} and set(bad.classifier) == {"Resources"} and len(bad) == 5


def test_probe_minutes():
    p = synth.probes(10, "CAM-05", 5, 8)
    pm = probe_minutes(p, synth.T0, synth.T0 + pd.Timedelta(minutes=10))
    assert_classifier_sums(pm)
    bad = pm[pm.status == "bad"]
    assert set(bad.classifier) == {"Unreachable"} and len(bad) == 3
    assert summarise(pm, APP).n == 30


def test_rpm_parse():
    xml = """<probe-results><probe-test-results><owner>shimpaku</owner><test-name>vxlan-l2</test-name>
    <target-address>10.100.0.42</target-address><probe-test-current-results><probe-test-generic-results>
    <probe-results-summary><probes-sent>5</probes-sent><probes-received>5</probes-received>
    <loss-percentage>0</loss-percentage><rtt-average>5651</rtt-average><rtt-jitter>1602</rtt-jitter>
    </probe-results-summary></probe-test-generic-results></probe-test-current-results></probe-test-results>
    </probe-results>"""
    (r,) = parse_probe_results("leaf1", xml)
    assert r["client_id"] == "rpm:leaf1:shimpaku/vxlan-l2" and r["ok"] and abs(r["rtt_ms"] - 5.651) < 1e-9


# ------------------------------------------------------------------ end-to-end phase-3 exit, synthetic
@pytest.mark.parametrize("fault,sle,cls", [
    ("FAB-01", FABRIC, "Redundancy"),
    ("FAB-02", FABRIC, "Adjacency"),
    ("FAB-04", FABRIC, "Adjacency"),
    ("FAB-05", APP, "Unreachable"),
    ("CAM-05", APP, "Unreachable"),
])
def test_incident_right_classifier_within_300s(fault, sle, cls):
    con = synth.make_db(MINUTES, fault, F_START, F_END)
    drive(con, MINUTES)
    rows = incidents_of(con)
    assert len(rows) == 1, rows
    iid, t_open, t_detect, t_close, got_sle, rc, status = rows[0]
    rc = json.loads(rc)
    assert (got_sle, rc[0]["classifier"]) == (sle, cls)
    t_eff = synth.T0 + pd.Timedelta(minutes=F_START)
    assert (pd.Timestamp(t_detect) - t_eff).total_seconds() <= 300
    assert status == "closed" and pd.Timestamp(t_close) == synth.T0 + pd.Timedelta(minutes=F_END)
    expected_fc = {"FAB-01": "link-down", "FAB-02": "link-down", "FAB-04": "mtu-mismatch",
                   "FAB-05": "bgp-overlay-down", "CAM-05": "acl-shadow-term"}[fault]
    assert expected_fc in [d["fault_class"] for d in rc]


def test_no_fault_no_incident():
    con = synth.make_db(MINUTES)
    res = drive(con, MINUTES)
    assert incidents_of(con) == []
    assert res[-1]["sle"][FABRIC]["pct"] == 100.0 and res[-1]["sle"][FABRIC]["n"] > 0


# ------------------------------------------------------------------ blast radius (R5 checks)
def _units(n_per, fail):
    rows = []
    for node in ["leaf1", "leaf2", "spine1", "spine2", "r1", "r2", "sw1"]:
        for k in range(n_per):
            rows.append(dict(node=node, role="leaf" if "leaf" in node else "other",
                             status="bad" if fail(node, k) else "good"))
    return pd.DataFrame(rows)


def test_blast_scoped():
    u = _units(12, lambda n, k: n == "leaf1" and k < 8)
    br = blast.blast_radius(u, ["node", "role"])
    assert br["verdict"] == "scoped" and br["scope"] == {**br["scope"], "feature": "node", "value": "leaf1"}


def test_blast_abstains_on_no_fault():
    rng = np.random.default_rng(1)
    for seed in range(20):
        r = np.random.default_rng(seed)
        u = _units(12, lambda n, k: r.random() < 0.02)
        assert blast.blast_radius(u, ["node", "role"])["verdict"] in ("diffuse", "no_failures")
    _ = rng


def test_blast_rare_value_refused():
    u = _units(12, lambda n, k: n == "r2" and k == 0)
    assert blast.blast_radius(u, ["node"])["verdict"] == "diffuse"


# ------------------------------------------------------------------ detectors
def test_binomial_multiwindow():
    idx = pd.date_range(synth.T0, periods=600, freq="min")
    pm = pd.DataFrame(dict(bad=0, n=7), index=idx)
    assert not detectors.binomial_multiwindow(pm, idx[-1])["alarm"]
    pm.iloc[-3:, 0] = 3
    assert detectors.binomial_multiwindow(pm, idx[-1])["alarm"]


def test_gauge_season_mad_and_hw():
    rng = np.random.default_rng(0)
    idx = pd.date_range(synth.T0, periods=12 * 24 * 2, freq="5min")
    s = pd.Series(30 + rng.normal(0, 1, len(idx)), index=idx)
    assert detectors.gauge_check(s, idx[-1])["method"] == "season_mad"
    s.iloc[-1] = 60
    assert detectors.gauge_check(s, idx[-1])["alarm"]
    idx3 = pd.date_range(synth.T0, periods=12 * 24 * 4, freq="5min")
    s3 = pd.Series(30 + 5 * np.sin(np.arange(len(idx3)) * 2 * np.pi / 288) + rng.normal(0, 1, len(idx3)), index=idx3)
    r = detectors.gauge_check(s3, idx3[-1])
    assert r["method"] == "holt_winters" and not r["alarm"]


def test_rule_tree_versioned():
    d = rules.diagnose([dict(status="bad", sle=APP, classifier="Unreachable", client_id="rpm:sw1:x", target="t")])
    assert d[0]["fault_class"] == "acl-shadow-term" and d[0]["rule_tree"] == rules.RULE_TREE_VERSION
    _ = incidents


def test_against_real_store_and_replay():
    from shimpaku.analytics.__main__ import replay
    from shimpaku.store import Store

    st = Store(":memory:").init()
    s = synth.samples(MINUTES, "FAB-02", F_START, F_END)
    st.insert_metrics(list(s.itertuples(index=False, name=None)))
    run_once(st, now=synth.T0 + pd.Timedelta(minutes=72, seconds=3))
    rows = st.query("SELECT status, classifier FROM device_minute WHERE status = 'bad'")
    assert rows and {r["classifier"] for r in rows} <= {"Adjacency", "Redundancy"}
    rep = replay(st.con, 30, synth.T0 + pd.Timedelta(minutes=MINUTES))
    assert [i["primary"]["classifier"] for i in rep["incidents"]] == ["Adjacency"]
    assert rep["sle"][FABRIC]["n"] == 7 * 30


def test_one_minute_cpu_spike_opens_nothing():
    """Live 2026-10-24: all 7 nodes read cpu.idle 0 for ~30 s when gNMI subscribed."""
    con = synth.make_db(MINUTES)
    con.execute("UPDATE metric_sample SET value = 0 WHERE metric = 'cpu.idle' AND ts >= ? AND ts < ?",
                [synth.T0 + pd.Timedelta(minutes=70), synth.T0 + pd.Timedelta(minutes=71)])
    drive(con, 80)
    assert incidents_of(con) == []
    bad = con.execute("SELECT count(*) FROM device_minute WHERE classifier = 'Resources'").fetchone()[0]
    assert bad == 7


def test_sustained_cpu_opens_resources_incident():
    con = synth.make_db(MINUTES)
    con.execute("UPDATE metric_sample SET value = 2 WHERE metric = 'cpu.idle' AND entity_id = 'r2' AND ts >= ?",
                [synth.T0 + pd.Timedelta(minutes=70)])
    drive(con, 78)
    (row,) = incidents_of(con)
    assert json.loads(row[5])[0]["classifier"] == "Resources"
