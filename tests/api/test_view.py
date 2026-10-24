"""api/view.py: real service payloads (live snapshot) -> the dashboard's UI contract."""

import asyncio
import copy
import json
from pathlib import Path

import pytest

from shimpaku.api import demo, view

SNAP = json.loads((Path(__file__).parents[1] / "fixtures" / "live_snapshot.json").read_text())
INC = "INC-20261024-0839-fabric"


def test_sle_summary_every_site_and_window():
    for site, wins in SNAP["sle"].items():
        for w, real in wins.items():
            ui = view.sle_summary(copy.deepcopy(real))
            assert ui["site"] == site and ui["window_min"] == int(w)
            assert ui["changes"] is None  # not collected: never an empty list pretending "no changes"
            assert {s["id"] for s in ui["sles"]} == set(real["sle"])
            for s in ui["sles"]:
                src = real["sle"][s["id"]]
                assert s["value_pct"] == src["pct"] and s["n"] == src["n"]
                assert s["goal_pct"] is None and s["series"] == []
                assert [c["share_pct"] for c in s["classifiers"]] == [c["share_pct"] for c in src["classifiers"]]
                assert all(isinstance(c["name"], str) for c in s["classifiers"])


def test_incidents():
    ui = view.incident_list(copy.deepcopy(SNAP["incidents"]))
    (i,) = ui["incidents"]
    src = SNAP["incidents"]["incidents"][0]
    assert i["id"] == src["incident_id"] and i["status"] == src["status"] and i["sle"] == src["sle"]
    assert i["opened_at"] == view.ts(src["t_open"]) and isinstance(i["opened_at"], float)
    top = max(src["root_causes"], key=lambda r: r["score"])
    assert i["summary"] == top["why"] and top["entity"] in i["title"]
    assert i["site"] == "fabric" and isinstance(i["impact"], dict)
    one = view.incident(copy.deepcopy(SNAP["incident"][INC]))
    assert one["id"] == INC and one["action_id"] is None


def test_blast_radius():
    real = SNAP["blast"][INC]
    ui = view.blast_radius(copy.deepcopy(real))
    for k, t in (("tree", list), ("timeline", list), ("affected", list), ("distribution", list), ("evidence", list)):
        assert isinstance(ui[k], t)
    assert ui["timeline"] == [] and ui["scores"] is None
    assert ui["verdict"] == real["blast_radius"]["verdict"]
    assert round(sum(n["share_pct"] for n in ui["tree"])) == 100
    assert [a["name"] for a in ui["affected"]] == real["blast_radius"]["affected"]
    assert all(a["impact_pct"] is None for a in ui["affected"])
    rows = {(d["dimension"], it["name"]): it for d in ui["distribution"] for it in d["items"]}
    for r in real["blast_radius"]["table"]:
        it = rows[(r["feature"], str(r["value"]))]
        assert (it["failures"], it["total"]) == (r["fails"], r["n"])
    assert len(ui["evidence"]) == len(real["root_causes"])


def test_action_list_statuses_faithful():
    ui = view.action_list(copy.deepcopy(SNAP["actions"]))
    by = {a["id"]: a for a in ui["actions"]}
    for a in SNAP["actions"]["actions"]:
        u = by[a["id"]]
        assert u["status"] == a["status"] and u["created_at"] == a["created"]
        assert u["proposed_by"] == a["caller"]["name"] and isinstance(u["title"], str)
    assert by["act-20261024-acdfc3"]["status"] == "recurred"
    assert by["act-20261024-60e123"]["status"] == "blocked" and by["act-20261024-60e123"]["approved_by"] is None
    assert by["act-20261024-634f65"]["approved_by"] == "operator"


@pytest.mark.parametrize("aid", list(SNAP["action"]))
def test_action_detail(aid):
    real = SNAP["action"][aid]
    ui = view.action(copy.deepcopy(real))
    assert ui["status"] == real["status"]
    assert ui["steps"][0] == {"state": "proposed", "t": real["created"]}
    assert ui["diff"] is None and ui["verifier"] is None
    assert len(ui["validation"]) == len(real["timeline"])
    assert [g["id"] for g in ui["guardrails"]] == real["policy"]["checked"]
    if real["commits"]:
        assert ui["committed_at"] == real["commits"][0]["ts"]
        assert ui["confirm_minutes"] == real["commits"][0]["confirm_min"]
    checks = (real["validation"] or {}).get("checks") or []
    assert (ui["series"] or []) == [{"t": c["ts"], "value": 100 if c["ok"] else 0} for c in checks]


def test_action_recurred_and_blocked():
    rec = view.action(copy.deepcopy(SNAP["action"]["act-20261024-acdfc3"]))
    assert rec["steps"][-1]["state"] == "recurred" and rec["steps"][-1]["t"]
    assert any(v["level"] == "bad" and "symptom back" in v["text"] for v in rec["validation"])
    assert any(p["value"] == 0 for p in rec["series"])
    blk = view.action(copy.deepcopy(SNAP["action"]["act-20261024-60e123"]))
    assert [s["state"] for s in blk["steps"]] == ["proposed", "blocked"]
    failed = [g for g in blk["guardrails"] if not g["passed"]]
    assert [g["id"] for g in failed] == ["G5"] and "in flight" in failed[0]["detail"]
    assert blk["confirm_minutes"] is None and blk["committed_at"] is None


def test_bench_selftest_truthful():
    real = SNAP["bench"]
    ui = view.bench(copy.deepcopy(real))
    sc = real["selftest"]["scenarios"]
    t = ui["selftest"]["totals"]
    assert t["scenarios"] == len(sc) == 16
    assert t["trials"] == sum(s["trials"] for s in sc.values()) == 80
    assert t["passes"] == sum(s["passes"] for s in sc.values()) == 80
    assert ui["published"] is False and "systems" not in ui  # no scored run: no made-up systems
    assert ui["runs"] == []


def test_inventory_topology_audit():
    inv = view.inventory(copy.deepcopy(SNAP["inventory"]))
    assert [d["name"] for d in inv["devices"]] == [n["name"] for n in SNAP["inventory"]["nodes"]]
    assert all(d["cpu_pct"] is None for d in inv["devices"])
    topo = view.topology(copy.deepcopy(SNAP["topology"]))
    assert len(topo["edges"]) == len(SNAP["topology"]["lldp"]) and "sw1" in topo["nodes"]
    au = view.audit(copy.deepcopy(SNAP["audit"]))
    assert [e["tool"] for e in au["entries"]] == [r["tool"] for r in SNAP["audit"]["rows"]]
    assert all(isinstance(e["t"], float) for e in au["entries"])


def _demo(fn, *a, **k):
    return asyncio.run(getattr(demo, fn)(*a, **k))


@pytest.mark.parametrize("vfn,dfn,args", [
    ("sle_summary", "sle_get_summary", ("campus",)), ("incident_list", "incident_list", ()),
    ("incident", "incident_get", ("INC-0107",)), ("blast_radius", "sle_get_blast_radius", ("INC-0108",)),
    ("action_list", "action_list", ()), ("action", "action_get", ("41",)), ("inventory", "inventory_list", ()),
    ("topology", "topo_get_neighbors", ()), ("audit", "audit_query", ()), ("bench", "bench_results", ()),
])
def test_demo_passes_through(vfn, dfn, args):
    d = _demo(dfn, *args)
    assert getattr(view, vfn)(copy.deepcopy(d)) == d


@pytest.mark.parametrize("vfn", ["sle_summary", "incident_list", "incident", "blast_radius", "action_list",
                                 "action", "inventory", "topology", "audit", "bench"])
def test_error_and_unavailable_pass_through(vfn):
    for p in ({"error": {"code": "x", "message": "y"}}, {"available": False, "reason": "off"}):
        assert getattr(view, vfn)(copy.deepcopy(p)) == p


def test_idempotent():
    for fn, real in (("sle_summary", SNAP["sle"]["fabric"]["1440"]), ("incident_list", SNAP["incidents"]),
                     ("blast_radius", SNAP["blast"][INC]), ("action_list", SNAP["actions"]),
                     ("action", SNAP["action"]["act-20261024-acdfc3"]), ("bench", SNAP["bench"]),
                     ("audit", SNAP["audit"]), ("topology", SNAP["topology"]), ("inventory", SNAP["inventory"])):
        once = getattr(view, fn)(copy.deepcopy(real))
        assert getattr(view, fn)(copy.deepcopy(once)) == once, fn
