from pathlib import Path

from shimpaku import topology
from shimpaku.store import Store

TOMLS = Path(__file__).parent / "fixtures"


def _topo():
    return topology.load([TOMLS / "day-02-switching.toml", TOMLS / "day-06-datacenter.toml"])


def test_load_sites_nodes_links():
    t = _topo()
    assert {n["name"] for n in t.nodes()} == {"r1", "r2", "sw1", "spine1", "spine2", "leaf1", "leaf2"}
    assert t.entities["leaf2"]["parent_id"] == "site:fabric" and t.entities["r1"]["parent_id"] == "site:campus"
    nb = {(x["interface"], x["peer"], x["peer_interface"]) for x in t.neighbors("leaf2")}
    assert nb == {("ge-0/0/0", "spine1", "ge-0/0/1"), ("ge-0/0/1", "spine2", "ge-0/0/1")}
    assert t.interfaces_of("sw1") == {"ge-0/0/0"}


def test_enrich_addresses_and_lldp():
    t = _topo()
    topology.enrich(t, lldp={"leaf2": [{"interface": "ge-0/0/0", "peer": "spine1", "port_id": "521"}]},
                    addresses={"leaf2": {"ge-0/0/0": ["10.0.12.2/30"], "ge-0/0/1": ["10.0.99.2/30"]}})
    e = {x["src"]: x for x in t.edges if x["relation"] == "connected_to"}
    assert e["leaf2:ge-0/0/0"]["attrs"]["address_check"] == "ok"
    assert e["leaf2:ge-0/0/0"]["attrs"]["lldp"] == "confirmed_device"
    assert e["leaf2:ge-0/0/1"]["attrs"]["address_check"] == "mismatch"


def test_sync_to_store():
    with Store(":memory:") as s:
        topology.sync(s, _topo())
        assert s.query("select count(*) n from entity where kind='device'")[0]["n"] == 7
