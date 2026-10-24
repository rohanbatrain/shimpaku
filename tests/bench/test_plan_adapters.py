import json

import httpx
import respx

from conftest import ROOT
from virga.adapters import FileAdapter, ShimpakuAdapter, make, norm_incident
from virga.baselines import classify_diff
from virga.catalogue import load
from virga.decoys import DecoyGen
from virga.plan import make_plan, null_per_block

HAVE = {"day-06-fabric", "day-02-campus"}


def test_plan_is_seeded_and_meets_r5_design():
    cat = load(ROOT / "bench" / "catalogue.yaml")
    ids = cat.benchable(HAVE, False)
    a = make_plan(cat, ids, seed=7, count=40)
    b = make_plan(cat, ids, seed=7, count=40)
    c = make_plan(cat, ids, seed=8, count=40)
    strip = lambda p: [{k: v for k, v in i.items()} for i in p["items"]]  # noqa: E731
    assert strip(a) == strip(b) and strip(a) != strip(c)
    faults = [i for i in a["items"] if i["kind"] == "fault"]
    assert len(faults) == 40
    assert a["null_share"] >= 0.2
    # each class once per complete block
    first_block = [i["scenario"] for i in faults if i["block"] == 1]
    assert sorted(first_block) == sorted(ids)
    # gaps: hold + 10-20 min + 1-59 s jitter; starts drift off minute boundaries
    items = a["items"]
    for x, y in zip(items, items[1:]):
        gap = y["t_offset_s"] - x["t_offset_s"] - x["hold_s"]
        assert 600 + 1 <= gap <= 1200 + 59
    assert len({i["t_offset_s"] % 60 for i in items}) > 5
    # intensities drawn inside their ranges
    for i in faults:
        for k, v in i["intensity"].items():
            lo, hi = cat.scenarios[i["scenario"]]["toggle"]["intensity"][k]["range"]
            assert lo <= v <= hi
    assert a["catalogue_sha256"] == cat.sha256


def test_null_per_block():
    assert null_per_block(14, 0.2) == 4      # 4/18 = 22%
    assert null_per_block(4, 0.2) == 1
    assert null_per_block(10, 0.0) == 0


def test_decoys_avoid_trial_nodes_in_confirmed_mode():
    cat = load(ROOT / "bench" / "catalogue.yaml")
    g = DecoyGen(cat.lab, seed=1, rate_per_trial=6)
    ds = g.schedule(0, 300, ["leaf1"], plain=False)
    assert ds and all(d["node"] != "leaf1" for d in ds)
    assert all(5 <= d["t_at"] <= 300 for d in ds)
    for d in ds:
        assert not any("virga" in ln or "aiops" in ln for ln in d["apply"])  # no tell-tale names on devices
    g2 = DecoyGen(cat.lab, seed=1, rate_per_trial=6)
    assert [d["apply"] for d in g2.schedule(0, 300, ["leaf1"], plain=False)] == [d["apply"] for d in ds]


def test_b0b_keyword_classifier():
    assert classify_diff("leaf1", "[edit interfaces ge-0/0/0 unit 0 family inet]\n+       mtu 1400;") == \
        ("intf:leaf1:ge-0/0/0", "mtu-mismatch")
    assert classify_diff("spine1", "[edit interfaces ge-0/0/0]\n+   disable;") == ("intf:spine1:ge-0/0/0", "link-down")
    assert classify_diff("leaf1", "[edit vlans v100 vxlan]\n-     vni 10100;\n+     vni 10199;")[1] == "vni-mismatch"
    assert classify_diff("r1", "[edit firewall family inet filter F-USERS-IN]\n+    term aiops-quarantine { then discard; }") == \
        ("node:r1", "acl-shadow-term")
    assert classify_diff("leaf1", "[edit policy-options]\n+   prefix-list pl-spare12 { 192.0.2.12/32; }")[1] == "config-change"


def test_norm_incident_variants():
    a = norm_incident({"id": 7, "opened_at": "2026-10-24T00:00:10Z",
                       "root_cause": {"entity": "intf:leaf1:ge-0/0/0", "kind": "mtu-mismatch"},
                       "blast_radius": {"entities": ["node:leaf1"]}})
    assert a["incident_id"] == "7" and a["root_causes"][0]["fault_class"] == "mtu-mismatch"
    assert a["blast_radius"] == ["node:leaf1"]
    b = norm_incident({"incident_id": "I-1", "t_detect": 1761800061000,
                       "root_causes": [{"entity": "x", "fault_class": "a", "score": 0.2},
                                       {"entity": "y", "fault_class": "b", "score": 0.9}]})
    assert b["t_detect"] == 1761800061.0 and b["root_causes"][0]["entity"] == "y"
    assert norm_incident({"no": "time"}) is None


def test_file_adapter(tmp_path):
    p = tmp_path / "audit.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in [
        {"t": "2026-10-24T00:00:00Z", "type": "incident", "root_cause": {"entity": "node:r1", "kind": "acl-shadow-term"}},
        {"t": "2026-10-24T00:01:00Z", "type": "action", "tool": "rollback_config", "args": {"node": "r1", "rollback_id": 1}},
        "not json"]) + "\n{bad")
    fa = FileAdapter(str(p))
    assert len(fa.incidents()) == 1 and fa.actions()[0]["node"] == "r1"
    assert make(f"file:{p}").path == p and make("none").incidents() == []


@respx.mock
def test_shimpaku_adapter_reads_contract_routes():
    respx.get("http://127.0.0.1:8780/api/incidents").mock(return_value=httpx.Response(200, json={"items": [
        {"incident_id": "I-1", "t_detect": 100.0, "root_causes": [{"entity": "node:leaf1", "fault_class": "vni-mismatch"}]}]}))
    respx.get("http://127.0.0.1:8780/api/actions").mock(return_value=httpx.Response(200, json=[
        {"id": "A1", "kind": "rollback", "args": {"device": "leaf1"}, "status": "executed", "created_at": 150},
        {"id": "A2", "kind": "rollback", "args": {"device": "leaf1"}, "status": "proposed", "created_at": 160}]))
    a = make("shimpaku")
    assert isinstance(a, ShimpakuAdapter)
    assert a.incidents()[0]["incident_id"] == "I-1"
    acts = a.actions(since=120)
    assert [x["action_id"] for x in acts] == ["A1"] and acts[0]["node"] == "leaf1"
