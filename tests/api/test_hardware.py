"""/api/hardware (routes_hardware): the twin's shape in demo mode, its sources over the real
service's raw shapes, guest redaction (no management address, no host block, no other VM names,
no interface descriptions), and the facts parser behind ``device_get_facts``."""

from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from shimpaku.api import demo, routes_hardware
from shimpaku.api.app import create_app
from shimpaku.api.auth import Principal
from shimpaku.devices import parse_facts

from test_viz import Raw, _ips  # noqa: E402  (same directory)


class FakeHost:
    """``app.state.hostmon``: a HostStore stand-in with other domains on the host."""

    def __init__(self):
        now = time.time()
        self.vms = {"vms": [{"id": "1", "name": "lab-r1", "state": "running"},
                            {"id": "2", "name": "lab-sw1", "state": "running"},
                            {"id": None, "name": "lab-leaf1", "state": "shut off"},
                            {"id": None, "name": "pve-staging", "state": "shut off"},
                            {"id": None, "name": "winsync-01", "state": "shut off"}], "error": None, "t": now}
        self._s = [{"t": now, "ncpu": 16, "mem_total_mb": 63996, "mem_avail_mb": 24950, "load": [20.1, 19.3, 18.0],
                    "temp_c": 95.0, "temp_crit_c": 100.0, "fans": [{"label": "msi fan1", "rpm": 3609}]}]

    def samples(self, since_s=0):
        return self._s


class RawHw(Raw):
    facts_calls = 0

    async def iface_get_errors(self, device, ifname=None):
        return {"device": device, "interfaces": [
            {"ifname": "ge-0/0/0", "admin": "up", "oper": "up", "description": "uplink to 10.99.0.1 secret-desc",
             "input_packets": 1789, "output_packets": 348, "input_errors": 0, "output_errors": 0, "input_drops": 0,
             "output_drops": 0, "carrier_transitions": 1, "fcs_errors": None, "mtu": "1514"}]}

    async def device_get_facts(self, device):
        RawHw.facts_calls += 1
        return {"device": device, "version": "26.2R1.7", "model": "vjunos", "uptime_s": 7200, "booted_at": 1.0}

    async def live_port_rates(self, at=None):
        return {"at": time.time(), "window_s": 90, "ports": {"sw1": {"ge-0/0/0": {"in_bps": 340.3, "out_bps": 125.5, "t": time.time()}}},
                "speed_bps": None, "source": "test"}


def _client(role: str, svc=None, host=None) -> TestClient:
    svc = svc or RawHw()

    async def call(name, *a, **kw):
        return await getattr(svc, name)(*a, **kw)

    def reader():
        if role == "operator":
            return Principal("session", "operator", "admin")
        return Principal("session" if role == "tester" else "anonymous", "guest", "read", role=role,
                         lease_id="L1" if role == "tester" else None)
    app = FastAPI()
    app.include_router(routes_hardware.router(call, reader, svc, hostmon=lambda: host))
    return TestClient(app)


# ---- demo -----------------------------------------------------------------------------------------
def test_demo_shape():
    c = TestClient(create_app(demo, auth_mode="off"))
    r = c.get("/api/hardware")
    assert r.status_code == 200
    d = r.json()
    assert d["demo"] is True and not _ips(r.text)
    assert {x["name"] for x in d["devices"]} == {"r1", "r2", "sw1", "spine1", "spine2", "leaf1", "leaf2"}
    assert set(d["models"]) == {"vjunos-switch", "vjunos-router"}
    assert d["models"]["vjunos-switch"]["equivalent"].startswith("EX9214")
    assert d["models"]["vjunos-router"]["equivalent"].startswith("MX")
    assert all(s["url"].startswith("https://www.juniper.net/documentation/") for m in d["models"].values() for s in m["sources"])
    hv = d["hypervisor"]
    assert hv["hosts"] == 1 and hv["devices"] == 7 and hv["declared_vcpu"] == 28 and hv["declared_memory_mb"] == 7 * 5120
    assert hv["guest_counts"] == {"running": 7} and "unrelated-vm" not in r.text
    assert hv["host"]["ncpu"] == 16      # auth off: the caller is the operator
    leaf1 = next(x for x in d["devices"] if x["name"] == "leaf1")
    p1 = next(p for p in leaf1["ports"] if p["ifname"] == "ge-0/0/1")
    assert p1["peer"] == "spine2" and p1["oper"] == "down" and p1["link_state"] != "up"
    sw1 = next(x for x in d["devices"] if x["name"] == "sw1")
    fx = next(p for p in sw1["ports"] if p["ifname"] == "ge-0/0/5")
    assert fx["fixture"] == {"vlan": 999, "present": True} and fx["role"] == "demo trunk"
    for x in d["devices"]:
        assert x["version"] == "26.2R1.7" and x["uptime_s"] > 0 and x["declared"] == {"vcpu": 4, "memory_mb": 5120}
        assert [p["ifname"] for p in x["ports"]] == sorted((p["ifname"] for p in x["ports"]), key=routes_hardware.port_sort)
        for p in x["ports"]:
            assert p["in_bps"] is None or p["in_bps"] < 100e6  # a vJunos VM forwards ≤ 100 Mbit/s in total


# ---- sources over the real service's shapes -------------------------------------------------------
def test_raw_service_sources_and_operator_host():
    c = _client("operator", host=FakeHost())
    d = c.get("/api/hardware").json()
    assert not _ips(json.dumps(d))
    sw1 = next(x for x in d["devices"] if x["name"] == "sw1")
    p0 = next(p for p in sw1["ports"] if p["ifname"] == "ge-0/0/0")
    # the live NETCONF read says up although the stored gNMI sample says down: live wins, source named
    assert p0["oper"] == "up" and p0["oper_source"].startswith("NETCONF")
    assert p0["in_bps"] == 340.3 and p0["counters"]["input_packets"] == 1789 and p0["peer"] == "r1"
    assert "description" not in json.dumps(d) and "secret-desc" not in json.dumps(d)
    assert sw1["version"] == "26.2R1.7" and sw1["uptime_s"] == 7200
    hv = d["hypervisor"]
    assert hv["guests"] == {"r1": "running", "sw1": "running", "leaf1": "shut off"}
    assert hv["guest_counts"] == {"running": 2, "shut off": 1}
    assert hv["host"] == {"t": hv["host"]["t"], "ncpu": 16, "mem_total_mb": 63996, "mem_avail_mb": 24950,
                          "load": [20.1, 19.3, 18.0], "temp_c": 95.0, "temp_crit_c": 100.0}
    blob = json.dumps(d)
    assert "pve-staging" not in blob and "winsync" not in blob and "fans" not in blob


@pytest.mark.parametrize("role", ["tester", "visitor"])
def test_guests_get_no_host_block_and_no_secrets(role):
    c = _client(role, host=FakeHost())
    r = c.get("/api/hardware")
    assert r.status_code == 200
    d = r.json()
    assert d["hypervisor"]["host"] is None
    assert d["hypervisor"]["guest_counts"] == {"running": 2, "shut off": 1}
    blob = r.text
    assert not _ips(blob) and "pve-staging" not in blob and "secret-desc" not in blob
    assert "config_inherited" not in blob and "vlan members" not in blob and "/home/" not in blob


def test_public_view_visitor_through_the_app(monkeypatch):
    from shimpaku.api.auth import hash_password
    monkeypatch.setenv("SHIMPAKU_ADMIN_PASSWORD_HASH", hash_password("pw-pw-pw-pw"))
    app = create_app(RawHw(), auth_mode="local", public_view=True)
    app.state.hostmon = FakeHost()
    c = TestClient(app)
    assert c.get("/api/whoami").json()["role"] == "visitor"
    r = c.get("/api/hardware")
    assert r.status_code == 200 and not _ips(r.text)
    assert r.json()["hypervisor"]["host"] is None and "pve-staging" not in r.text


def test_no_sampler_and_no_facts_say_so():
    svc = Raw()  # no device_get_facts, no live_port_rates
    c = _client("operator", svc=svc, host=None)
    d = c.get("/api/hardware").json()
    hv = d["hypervisor"]
    assert hv["guests"] == {} and "not running" in hv["guests_reason"] and hv["host"] is None
    for x in d["devices"]:
        assert x["version"] is None and x["facts_reason"] and x["uptime_s"] is None


def test_facts_are_cached(monkeypatch):
    RawHw.facts_calls = 0
    monkeypatch.setattr(routes_hardware, "TTL_S", 0.0)  # every request rebuilds the payload
    c = _client("operator")
    c.get("/api/hardware")
    assert RawHw.facts_calls == 5  # one per device in Raw's inventory
    c.get("/api/hardware")
    assert RawHw.facts_calls == 5  # within the 15 min facts TTL the rebuild does not read the devices again


# ---- pure helpers -----------------------------------------------------------------------------------
def test_build_ports_keeps_physical_ports_and_names_sources():
    links = [{"id": "r1-sw1", "a": "r1", "a_if": "ge-0/0/1", "b": "sw1", "b_if": "ge-0/0/0", "role": "trunk",
              "state": "up", "evidence": {"lldp": "seen", "oper_b": "up"}}]
    gnmi = {"ge-0/0/0": "up", "ge-0/0/3": "down"}
    fixture = {"device": "sw1", "ifname": "ge-0/0/5", "vlan": 999, "present": False, "oper": "down", "admin": "up"}
    ports = routes_hardware.build_ports("sw1", links, {k: v for k, v in {**gnmi, "lo0": "up", "irb": "up"}.items()
                                                       if routes_hardware.PORT_RE.match(k)}, {}, None, fixture)
    assert [p["ifname"] for p in ports] == ["ge-0/0/0", "ge-0/0/3", "ge-0/0/5"]
    assert ports[0]["peer"] == "r1" and ports[0]["lldp"] == "seen" and ports[0]["oper_source"].startswith("gNMI")
    assert ports[1]["wired"] is False and ports[1]["oper"] == "down" and ports[1]["counters"] is None
    assert ports[2]["fixture"] == {"vlan": 999, "present": False} and ports[2]["oper"] == "down"


def test_guest_states_only_lab_devices():
    vms = {"vms": [{"name": "lab-r1", "state": "running"}, {"name": "sw1", "state": "paused"},
                   {"name": "other", "state": "running"}], "error": None, "t": 5.0}
    g = routes_hardware.guest_states(vms, ["r1", "sw1", "leaf2"])
    assert g == {"states": {"r1": "running", "sw1": "paused"}, "sampled_at": 5.0, "reason": None}
    assert routes_hardware.guest_states({"vms": [], "error": "error: failed to connect to 10.99.0.1", "t": 1.0},
                                        ["r1"])["reason"] == "error: failed to connect to <host>"


def test_parse_facts():
    sw = ET.fromstring("<software-information><host-name>r1</host-name><product-model>vjunos-router</product-model>"
                       "<junos-version>26.2R1.7</junos-version></software-information>")
    up = ET.fromstring('<system-uptime-information><system-booted-time><date-time seconds="1000">x</date-time>'
                       "<time-length>1d</time-length></system-booted-time></system-uptime-information>")
    assert parse_facts(sw, up, now=4600) == {"version": "26.2R1.7", "model": "vjunos-router", "uptime_s": 3600, "booted_at": 1000.0}
    old = ET.fromstring("<software-information><package-information><name>junos</name>"
                        "<comment>JUNOS Software Release [21.4R3.15]</comment></package-information></software-information>")
    assert parse_facts(old, None)["version"] == "21.4R3.15"
    assert parse_facts(ET.fromstring("<x/>"), ET.fromstring("<y/>")) == {"version": None, "model": None, "uptime_s": None, "booted_at": None}
