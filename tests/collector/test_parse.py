import json
from pathlib import Path

from shimpaku.collector import gnmic_config
from shimpaku.collector.parse import JSONStream, NodeParser, allowed_event

FIX = Path(__file__).parent / "fixtures"


def _objs(name, chunk=997):
    s = (FIX / name).read_text()
    js, out = JSONStream(), []
    for i in range(0, len(s), chunk):  # arbitrary chunk boundaries, as a pipe delivers them
        out += js.feed(s[i:i + chunk])
    assert js.buf.strip() == ""
    return out


def test_stream_decoder_handles_split_values():
    js = JSONStream()
    assert js.feed('{"a": 1}\n{"b"') == [{"a": 1}]
    assert js.feed(': [1, 2]}\n') == [{"b": [1, 2]}]


def test_sample_fixture_metrics():
    p = NodeParser("leaf2", {"ge-0/0/0", "ge-0/0/1"})
    for o in _objs("gnmic_leaf2_sample.json"):
        p.handle(o, rx=0)
    m, _ = p.take()
    ents = {e for _, e, _, _ in m}
    mets = {x for _, _, x, _ in m}
    assert {"leaf2:ge-0/0/0", "leaf2:ge-0/0/1", "leaf2:irb", "leaf2:lo0"} <= ents
    assert "leaf2:ge-0/0/10" not in ents  # down port outside the topology is not stored
    assert {"if.in_octets", "if.oper_up", "cpu.user", "mem.used_pct", "ospf.full", "bgp.established", "bfd.up"} <= mets
    assert ("leaf2:bfd:10.0.22.1" in ents) and ("leaf2:bgp:10.255.0.41" in ents) and ("leaf2:ospf:ge-0/0/1.0" in ents)
    full = [v for _, e, x, v in m if x == "ospf.full"]
    assert full and all(v == 1.0 for v in full)


def test_events_assembled_and_filtered():
    p = NodeParser("leaf2")
    for o in _objs("gnmic_leaf2_linkflap.json"):
        p.handle(o, rx=1792800000.0)
    p.flush_events()
    _, ev = p.take()
    types = {e["type"] for e in ev}
    assert "SNMP_TRAP_LINK_DOWN" in types and "SNMP_TRAP_LINK_UP" in types
    assert not any(t.startswith(("UI_LOGIN", "UI_AUTH", "UI_PERMS", "SYSTEM", "AGENTD")) for t in types)
    down = next(e for e in ev if e["type"] == "SNMP_TRAP_LINK_DOWN")
    assert down["entity_id"].startswith("leaf2:ge-0/0/") and down["attrs"]["interface-name"].startswith("ge-0/0/")
    assert down["ts"].year == 2026 and p.dropped_events > 0


def test_allowlist():
    assert allowed_event("RPD_OSPF_NBRDOWN") and allowed_event("BFDD_STATE_UP_TO_DOWN")
    assert allowed_event("UI_COMMIT_COMPLETED")
    assert not allowed_event("UI_LOGIN_EVENT") and not allowed_event("UI_NETCONF_CMD") and not allowed_event("SYSTEM")


def test_gnmic_config_has_no_secret():
    cfg = json.loads(gnmic_config("leaf2", "10.99.0.42", 32767))
    assert "password" not in json.dumps(cfg).lower()
    assert cfg["subscriptions"]["if"]["sample-interval"] == "10s"
    assert cfg["subscriptions"]["ev"]["stream-mode"] == "target-defined"
