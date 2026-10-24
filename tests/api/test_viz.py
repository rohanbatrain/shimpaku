"""/api/viz/* (routes_viz): shapes, live-state rules, guest safety, and the store providers behind
the device view (device_get_health, iface_get_history)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from shimpaku import service
from shimpaku.api import demo, routes_viz
from shimpaku.api.app import create_app

INTENDED = [
    {"device": "r1", "ifname": "ge-0/0/0", "peer": "r2", "link": "r1-r2", "role": "routed"},
    {"device": "r2", "ifname": "ge-0/0/0", "peer": "r1", "link": "r1-r2", "role": "routed"},
    {"device": "r1", "ifname": "ge-0/0/1", "peer": "sw1", "link": "r1-sw1", "role": "trunk"},
    {"device": "sw1", "ifname": "ge-0/0/0", "peer": "r1", "link": "r1-sw1", "role": "trunk"},
    {"device": "leaf1", "ifname": "ge-0/0/0", "peer": "spine1", "link": "leaf1-spine1", "role": "fabric"},
    {"device": "spine1", "ifname": "ge-0/0/0", "peer": "leaf1", "link": "leaf1-spine1", "role": "fabric"},
]


class Raw:
    """A service stand-in that returns the real service's raw shapes (mgmt_ip included)."""

    REPLAY = False
    vlan_lines = ["set vlans shimpaku-demo-999 interface ge-0/0/5"]

    def __init__(self, **kw):
        self.__dict__.update(kw)

    async def inventory_list(self, site=None):
        nodes = [("r1", "campus", "vjunos-router", "10.99.0.11"), ("r2", "campus", "vjunos-router", "10.99.0.12"),
                 ("sw1", "campus", "vjunos-switch", "10.99.0.21"), ("leaf1", "fabric", "vjunos-switch", "10.99.0.41"),
                 ("spine1", "fabric", "vjunos-switch", "10.99.0.31")]
        return {"nodes": [{"name": n, "site": s, "kind": k, "mgmt_ip": ip, "topology": "t"} for n, s, k, ip in nodes],
                "sites": ["campus", "fabric"]}

    async def topo_get_neighbors(self, device=None):
        return {"intended": INTENDED, "lldp": [
            {"device": "leaf1", "local_if": "ge-0/0/0", "remote_system": "spine1", "remote_port": "x"},
            {"device": "r1", "error": "ConnectTimeoutError"}]}

    async def routing_get_health(self, device=None):
        devs = [
            {"device": "r1", "error": "ConnectError: 10.99.0.11 unreachable"},
            {"device": "r2", "ospf": [{"interface": "ge-0/0/0.0", "state": "Init", "address": "10.0.12.2"}], "bgp": [], "bfd": []},
            {"device": "sw1", "ospf": [], "bgp": [], "bfd": []},
            {"device": "leaf1", "ospf": [{"interface": "ge-0/0/0.0", "state": "Full"}], "bgp": [],
             "bfd": [{"interface": "ge-0/0/0.0", "state": "Up"}]},
            {"device": "spine1", "ospf": [{"interface": "ge-0/0/0.0", "state": "Full"}], "bgp": [],
             "bfd": [{"interface": "ge-0/0/0.0", "state": "Up"}]},
        ]
        return {"devices": [d for d in devs if device in (None, d["device"])]}

    async def device_get_health(self, device=None, window_min=60):
        return {"window_min": window_min, "devices": [
            {"device": "sw1", "cpu_pct": 12.0, "mem_pct": 40.0, "sampled_at": 1.0, "minutes": {"n": 10, "good": 10, "bad": 0},
             "classifiers": [], "last": {"status": "good"}, "ports": {"ge-0/0/0": "down"}}]}

    async def port_get_config(self, device, ifname):
        return {"device": device, "ifname": ifname, "admin": "up", "oper": "down", "config_inherited":
                "set interfaces ge-0/0/5 unit 0 family ethernet-switching vlan members shimpaku-demo-998",
                "config_own": "", "vlan_lines": self.vlan_lines, "interface_range_lines": []}

    async def incident_list(self, status=None):
        return {"incidents": [{"incident_id": "INC-20261024-0839-fabric", "t_open": "2026-10-24 08:39:00", "status": "open",
                               "sle": "fabric_health", "root_causes": [{"entity": "leaf1:bfd:10.0.11.1", "fault_class": "link-down", "score": 1}]}]}

    async def action_list(self, status=None):
        return {"actions": []}

    async def iface_get_errors(self, device, ifname=None):
        return {"device": device, "interfaces": [{"ifname": "ge-0/0/0", "oper": "up", "input_errors": 0}]}

    async def iface_get_history(self, device, window_min=60):
        return {"available": False, "reason": "no interface counters stored"}

    async def sle_get_summary(self, site, window_min=60):
        return {"available": False, "reason": "no data source registered yet"}

    async def audit_verify(self):
        return {"ok": True, "rows": 43}


def _ips(blob: str) -> bool:
    return "10.99." in blob or "mgmt_ip" in blob


def test_demo_topology_shape_and_no_mgmt_ip():
    c = TestClient(create_app(demo, auth_mode="off"))
    r = c.get("/api/viz/topology")
    assert r.status_code == 200
    d = r.json()
    assert d["demo"] is True and set(d["sites"]) == {"campus", "fabric"}
    assert {n["name"] for n in d["nodes"]} == {"r1", "r2", "sw1", "spine1", "spine2", "leaf1", "leaf2"}
    assert {n["role"] for n in d["nodes"]} == {"router", "switch", "spine", "leaf"}
    assert all(lk["state"] in ("up", "degraded", "down", "unknown") for lk in d["links"])
    assert next(lk for lk in d["links"] if lk["id"] == "leaf1-spine2")["state"] != "up"
    assert d["fixture"]["device"] == "sw1" and d["fixture"]["vlan"] == 999
    assert not _ips(r.text)
    for path in ("/api/viz/overview", "/api/viz/devices/sw1"):
        r = c.get(path)
        assert r.status_code == 200 and not _ips(r.text), path


def test_raw_service_rules():
    c = TestClient(create_app(Raw(), auth_mode="off"))
    d = c.get("/api/viz/topology").json()
    assert not _ips(json.dumps(d))  # inventory carried mgmt_ip; the routing error carried an address
    links = {lk["id"]: lk for lk in d["links"]}
    assert links["leaf1-spine1"]["state"] == "up"
    assert links["r1-r2"]["state"] == "down"           # OSPF Init on r2, r1 unreadable: nothing good
    assert links["r1-sw1"]["state"] == "down"           # sw1 ge-0/0/0 stored oper down
    nodes = {n["name"]: n for n in d["nodes"]}
    assert nodes["r1"]["state"] == "down" and nodes["r1"]["reachable"] is False
    assert nodes["leaf1"]["incidents"] == ["INC-20261024-0839-fabric"]
    assert nodes["sw1"]["health"]["cpu_pct"] == 12.0
    assert d["fixture"]["present"] is True and d["fixture"]["oper"] == "down"
    dev = c.get("/api/viz/devices/r1").json()
    assert dev["routing"]["available"] is False and "10.99" not in dev["routing"]["reason"]
    assert dev["history"]["available"] is False   # honest: not collected, not invented
    assert c.get("/api/viz/devices/../etc").status_code == 404


def test_fixture_vlan_missing_and_unknown_evidence():
    svc = Raw(vlan_lines=[])
    d = TestClient(create_app(svc, auth_mode="off")).get("/api/viz/topology").json()
    assert d["fixture"]["present"] is False
    # no LLDP, no protocol, no oper data -> unknown, never "up"
    lk = routes_viz.link_state({"id": "x", "a": "a", "a_if": "ge-0/0/0", "b": "b", "b_if": "ge-0/0/0", "role": "trunk"},
                               [], {}, {})
    assert lk["state"] == "unknown"


def test_overview_counts_are_from_payloads():
    o = TestClient(create_app(Raw(), auth_mode="off")).get("/api/viz/overview").json()
    assert o["devices"]["total"] == 5 and o["devices"]["reachable"] == 4
    assert o["incidents"]["open"] == 1
    assert o["audit"] == {"ok": True, "rows": 43, "first_bad": None}
    assert o["sles"] == []                                  # provider unavailable -> nothing shown
    assert o["actions_today"]["total"] == 0


def test_guest_visitor_gets_no_mgmt_ip():
    class Replay(Raw):
        REPLAY = True

        def info(self):
            return {"taken_at": 1.0}

    c = TestClient(create_app(Replay(), auth_mode="local"))
    assert c.get("/api/whoami").json()["role"] == "visitor"
    for path in ("/api/viz/topology", "/api/viz/overview", "/api/viz/devices/sw1"):
        r = c.get(path)
        assert r.status_code == 200 and not _ips(r.text), path


def test_viz_needs_sign_in(monkeypatch):
    from shimpaku.api.auth import hash_password
    monkeypatch.setenv("SHIMPAKU_ADMIN_PASSWORD_HASH", hash_password("pw-pw-pw-pw"))
    c = TestClient(create_app(demo, auth_mode="local"))
    for path in ("/api/viz/topology", "/api/viz/overview", "/api/viz/devices/sw1"):
        assert c.get(path).status_code == 401


# ---- store providers --------------------------------------------------------------------------
@pytest.fixture
def store(monkeypatch):
    con = duckdb.connect(":memory:")
    schema = Path(__file__).resolve().parents[2] / "src/shimpaku/store/schema.sql"
    con.execute(schema.read_text())
    saved = dict(service._providers)
    from shimpaku import providers
    providers.register_all(con)
    yield con
    service._providers.clear()
    service._providers.update(saved)


def test_iface_history_rates_and_counter_reset(store):
    now = datetime.now(timezone.utc).replace(tzinfo=None, second=30, microsecond=0)
    rows = []
    octets = [1000, 61000, 121000, 500, 60500]  # a reset between minute 2 and 3
    for i, v in enumerate(octets):
        t = now - timedelta(minutes=len(octets) - i)
        rows.append((t, "sw1:ge-0/0/0", "if.in_octets", float(v)))
        rows.append((t, "sw1:ospf:ge-0/0/0", "if.in_octets", 5.0))  # not a port: ignored
    store.executemany("INSERT INTO metric_sample VALUES (?, ?, ?, ?)", rows)
    out = asyncio.run(service.iface_get_history("sw1", 30))
    (ser,) = [i["series"] for i in out["interfaces"] if i["ifname"] == "ge-0/0/0"]
    bps = [p["in_bps"] for p in ser]
    assert bps == [8000.0, 8000.0, 8000.0]   # 60000 octets / 60 s * 8; the reset gives no point
    assert out["step_s"] == 60


def test_iface_history_absent_is_not_invented(store):
    out = asyncio.run(service.iface_get_history("r1", 60))
    assert out["available"] is False


def test_device_health_from_store(store):
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    store.executemany("INSERT INTO metric_sample VALUES (?, ?, ?, ?)", [
        (now - timedelta(seconds=30), "sw1", "cpu.idle", 88.0), (now - timedelta(seconds=30), "sw1", "mem.used_pct", 41.5),
        (now - timedelta(seconds=20), "sw1:ge-0/0/0", "if.oper_up", 1.0), (now - timedelta(seconds=20), "sw1:ge-0/0/5", "if.oper_up", 0.0)])
    store.executemany("INSERT INTO device_minute VALUES (?, ?, ?, ?, ?, ?, ?)", [
        (now - timedelta(minutes=3), "sw1", "fabric_health", "good", None, None, "{}"),
        (now - timedelta(minutes=2), "sw1", "fabric_health", "bad", "Adjacency", "Neighbour down", "{}"),
        (now - timedelta(minutes=1), "sw1", "fabric_health", "good", None, None, "{}")])
    out = asyncio.run(service.device_get_health("sw1", 60))
    (d,) = out["devices"]
    assert d["cpu_pct"] == 12.0 and d["mem_pct"] == 41.5
    assert d["minutes"] == {"n": 3, "good": 2, "bad": 1}
    assert d["last"]["status"] == "good"
    assert d["ports"] == {"ge-0/0/0": "up", "ge-0/0/5": "down"}
    assert d["classifiers"] == [{"classifier": "Adjacency", "sub_classifier": "Neighbour down", "minutes": 1}]
