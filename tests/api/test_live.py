"""/api/live/* (routes_live): link traffic, the event stream and time travel — shapes, the store
providers behind them (providers_live), and guest redaction."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from shimpaku import service
from shimpaku.api import demo, routes_live
from shimpaku.api.app import create_app
from shimpaku.api.auth import Principal

from test_viz import Raw, _ips  # noqa: E402  (same directory)
import re

_IPV4 = re.compile(r"\b\d{1,3}(\.\d{1,3}){3}\b")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


@pytest.fixture
def store():
    con = duckdb.connect(":memory:")
    con.execute((Path(__file__).resolve().parents[2] / "src/shimpaku/store/schema.sql").read_text())
    saved = dict(service._providers)
    from shimpaku import providers
    providers.register_all(con)
    yield con
    service._providers.clear()
    service._providers.update(saved)


def _sse_first(c: TestClient, path: str, event: str) -> dict | list:
    with c.stream("GET", path) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        cur = None
        for line in r.iter_lines():
            if line.startswith("event:"):
                cur = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and cur == event:
                return json.loads(line.split(":", 1)[1])
    raise AssertionError("no event")


# ---- demo shapes --------------------------------------------------------------------------------
def test_demo_traffic_shape():
    c = TestClient(create_app(demo, auth_mode="off"))
    d = c.get("/api/live/traffic").json()
    assert d["demo"] and d["live"] and d["speed_bps"] is None and d["speed_reason"]
    ids = {lk["id"] for lk in d["links"]}
    assert "leaf1-spine2" in ids and len(ids) == 6
    for lk in d["links"]:
        assert set(lk) >= {"ab_bps", "ba_bps", "sample_t", "stale", "util_ab", "util_ba"}
        assert lk["util_ab"] is None   # speed unknown: never a guessed utilisation
    down = next(lk for lk in d["links"] if lk["id"] == "leaf1-spine2")
    assert down["ab_bps"] == 0.0
    assert not _ips(json.dumps(d))


def test_demo_traffic_sse():
    c = TestClient(create_app(demo, auth_mode="off"))
    d = _sse_first(c, "/api/live/stream/traffic", "traffic")
    assert d["links"] and d["live"]


def test_demo_events_filters_and_stream():
    c = TestClient(create_app(demo, auth_mode="off"))
    d = c.get("/api/live/events", params={"limit": 50}).json()
    ev = d["events"]
    assert ev and [e["id"] for e in ev] == sorted((e["id"] for e in ev), reverse=True)   # newest first
    assert {"id", "t", "device", "type", "kind", "severity", "message"} <= set(ev[0])
    only = c.get("/api/live/events", params={"device": "leaf1", "kind": "ospf,bfd"}).json()["events"]
    assert only and all(e["device"] == "leaf1" and e["kind"] in ("ospf", "bfd") for e in only)
    txt = c.get("/api/live/events", params={"q": "drop-ospf"}).json()["events"]
    assert txt and all("drop-ospf" in e["message"] for e in txt)
    sev = c.get("/api/live/events", params={"severity": "warning"}).json()["events"]
    assert sev and all(e["severity"] == "warning" for e in sev)
    hello = _sse_first(c, "/api/live/stream/events", "hello")
    assert hello["last_id"] >= ev[0]["id"]
    batch = _sse_first(c, f"/api/live/stream/events?after_id={ev[3]['id']}", "events")
    assert batch and all(e["id"] > ev[3]["id"] for e in batch)


def test_demo_time_travel():
    c = TestClient(create_app(demo, auth_mode="off"))
    rg = c.get("/api/live/range").json()
    assert rg["from"] and rg["max_history_s"] == 86400
    before = c.get("/api/live/at", params={"t": time.time() - 40 * 60}).json()
    after = c.get("/api/live/at", params={"t": time.time() - 5 * 60}).json()
    st = lambda d, i: next(lk["state"] for lk in d["links"] if lk["id"] == i)  # noqa: E731
    assert st(before, "leaf1-spine2") == "up" and st(after, "leaf1-spine2") != "up"
    assert {n["name"]: n["state"] for n in after["nodes"]}["leaf1"] == "degraded"
    assert before["sles"] and all(s["value_pct"] is not None for s in before["sles"])
    assert after["minute"] % 60 == 0 and after["traffic"]["links"]
    assert not _ips(json.dumps([before["links"], before["nodes"]]))


def test_live_needs_sign_in(monkeypatch):
    from shimpaku.api.auth import hash_password
    monkeypatch.setenv("SHIMPAKU_ADMIN_PASSWORD_HASH", hash_password("pw-pw-pw-pw"))
    c = TestClient(create_app(demo, auth_mode="local", public_view=False))
    for path in ("/api/live/traffic", "/api/live/events", "/api/live/range", "/api/live/at?t=1"):
        assert c.get(path).status_code == 401, path


def test_without_a_source_is_unavailable():
    saved = dict(service._providers)
    service._providers.clear()
    try:
        c = TestClient(create_app(Raw(), auth_mode="off"))
        d = c.get("/api/live/traffic").json()
        assert d["available"] is False and d["links"] == []
        assert c.get("/api/live/events").json()["available"] is False
        assert c.get("/api/live/at", params={"t": time.time()}).json()["available"] is False
    finally:
        service._providers.update(saved)


# ---- store providers ----------------------------------------------------------------------------
def test_rates_from_counters_and_reset(store):
    now = _now()
    rows = [
        (now - timedelta(seconds=25), "r1:ge-0/0/0", "if.in_octets", 1000.0),
        (now - timedelta(seconds=15), "r1:ge-0/0/0", "if.in_octets", 11000.0),   # 10000 B / 10 s = 8000 bps
        (now - timedelta(seconds=25), "r1:ge-0/0/0", "if.out_octets", 9000.0),
        (now - timedelta(seconds=15), "r1:ge-0/0/0", "if.out_octets", 10.0),     # reset: no rate
        (now - timedelta(seconds=15), "r2:ge-0/0/0", "if.in_octets", 5.0),       # one sample: no rate
        (now - timedelta(seconds=15), "r1:lo0", "if.in_octets", 5.0),            # not a port
    ]
    store.executemany("INSERT INTO metric_sample VALUES (?, ?, ?, ?)", rows)
    out = asyncio.run(service._call_provider("live_port_rates", at=None))
    p = out["ports"]["r1"]["ge-0/0/0"]
    assert p["in_bps"] == 8000.0 and p["out_bps"] is None
    assert "lo0" not in out["ports"]["r1"] and out["speed_bps"] is None
    c = TestClient(create_app(Raw(), auth_mode="off"))
    lk = next(x for x in c.get("/api/live/traffic").json()["links"] if x["id"] == "r1-r2")
    assert lk["ab_bps"] is None and lk["ba_bps"] == 8000.0   # r1 in = r2 → r1
    # as of a minute ago there were no samples yet
    old = asyncio.run(service._call_provider("live_port_rates", at=time.time() - 300))
    assert old["ports"] == {}


def test_events_from_store_and_guest_redaction(store):
    now = _now()
    evs = [
        (now - timedelta(seconds=30), "r1", "r1:ge-0/0/0", "SNMP_TRAP_LINK_DOWN", {"priority": "warning", "interface-name": "ge-0/0/0"},
         "ifIndex 1, ifOperStatus down(2), ifName ge-0/0/0"),
        (now - timedelta(seconds=20), "sw1", "sw1", "UI_CFG_AUDIT_SET", {"user": "lab", "message": "x"},
         "User 'lab' set: [system root-authentication encrypted-password] -> \"$6$abc\""),
        (now - timedelta(seconds=10), "leaf1", "leaf1", "UI_COMMIT", {"hostname": "10.99.0.41"},
         "User 'shimpaku' requested 'commit' operation from 10.99.0.5"),
    ]
    store.executemany("INSERT INTO event (ts, ingest_ts, source, node, entity_id, type, attrs, raw) VALUES (?, ?, 'gnmi', ?, ?, ?, ?, ?)",
                      [(t, t, n, e, ty, json.dumps(a), raw) for t, n, e, ty, a, raw in evs])
    op = TestClient(create_app(Raw(), auth_mode="off")).get("/api/live/events").json()
    assert [e["type"] for e in op["events"]] == ["UI_COMMIT", "UI_CFG_AUDIT_SET", "SNMP_TRAP_LINK_DOWN"]
    link = op["events"][2]
    assert link["kind"] == "link" and link["severity"] == "warning" and link["interface"] == "ge-0/0/0"
    assert op["stored"] == 3 and op["last_id"] == op["events"][0]["id"]
    for role in ("tester", "visitor"):
        c = _router_client(role)
        out = c.get("/api/live/events").json()
        blob = json.dumps(out)
        assert not _IPV4.search(blob) and "encrypted-password" not in blob and "$6$" not in blob, role
        cfg = next(e for e in out["events"] if e["type"] == "UI_CFG_AUDIT_SET")
        assert cfg["message"] == routes_live.CONFIG_HIDDEN and "user" not in cfg["attrs"]
        after = c.get("/api/live/events", params={"after_id": op["events"][1]["id"]}).json()["events"]
        assert [e["type"] for e in after] == ["UI_COMMIT"]


def test_state_at_and_range(store):
    now = _now()
    t_fault = now - timedelta(minutes=5)
    rows = []
    for m in range(20, 0, -1):
        t = now - timedelta(minutes=m)
        down = t >= t_fault
        rows += [(t, "leaf1:ge-0/0/0", "if.oper_up", 0.0 if down else 1.0),
                 (t, "spine1:ge-0/0/0", "if.oper_up", 1.0),
                 (t, "leaf1:ospf:ge-0/0/0.0", "ospf.full", 0.0 if down else 1.0),
                 (t, "spine1:ospf:ge-0/0/0.0", "ospf.full", 0.0 if down else 1.0),
                 (t, "leaf1", "collector.connected", 1.0), (t, "spine1", "collector.connected", 1.0),
                 (t, "leaf1:ge-0/0/0", "if.in_octets", float(m * -1000 + 100000))]
    store.executemany("INSERT INTO metric_sample VALUES (?, ?, ?, ?)", rows)
    store.executemany("INSERT INTO device_minute VALUES (?, ?, ?, ?, ?, ?, ?)", [
        ((now - timedelta(minutes=10)).replace(second=0), "leaf1", "fabric_health", "good", None, None, json.dumps({"site": "fabric"})),
        ((now - timedelta(minutes=2)).replace(second=0), "leaf1", "fabric_health", "bad", "Adjacency", "Neighbour down", json.dumps({"site": "fabric"}))])
    c = TestClient(create_app(Raw(), auth_mode="off"))
    rg = c.get("/api/live/range").json()
    assert rg["samples"]["interval_s"] == 10 and rg["from"] is not None and "none" in rg["retention"]
    before = c.get("/api/live/at", params={"t": (now - timedelta(minutes=10)).replace(tzinfo=timezone.utc).timestamp()}).json()
    after = c.get("/api/live/at", params={"t": (now - timedelta(minutes=1)).replace(tzinfo=timezone.utc).timestamp()}).json()
    lk = lambda d: next(x for x in d["links"] if x["id"] == "leaf1-spine1")  # noqa: E731
    assert lk(before)["state"] == "up" and lk(after)["state"] == "down"
    nd = lambda d: next(x for x in d["nodes"] if x["name"] == "leaf1")  # noqa: E731
    assert nd(before)["state"] == "up" and nd(after)["state"] == "degraded"
    assert nd(after)["verdict"]["classifier"] == "Adjacency"
    assert routes_live.BFD_NOTE in after["notes"]
    assert not _ips(json.dumps(after))


# ---- guests through the router ------------------------------------------------------------------
def _router_client(role: str, svc=None) -> TestClient:
    svc = svc or Raw()

    async def call(name, *a, **kw):
        return await getattr(svc, name)(*a, **kw)

    def reader():
        return Principal("session" if role == "tester" else "anonymous", "guest", "read", role=role,
                         lease_id="L1" if role == "tester" else None)
    app = FastAPI()
    app.include_router(routes_live.router(call, reader, svc))
    return TestClient(app)


@pytest.mark.parametrize("role", ["tester", "visitor"])
def test_guest_demo_events_hide_config_text(role):
    c = _router_client(role, demo)
    blob = json.dumps(c.get("/api/live/events", params={"limit": 200}).json())
    assert "firewall family inet" not in blob and routes_live.CONFIG_HIDDEN in blob
    assert not _IPV4.search(blob)
    for path in ("/api/live/traffic", "/api/live/range", f"/api/live/at?t={time.time() - 60}"):
        r = c.get(path)
        assert r.status_code == 200 and not _ips(r.text), path
