"""Replay: a snapshot exported from a running app serves the same reads, refuses every write,
signs nobody in, and leaks no redacted term."""

import json

import pytest
from fastapi.testclient import TestClient

from shimpaku.api import demo, replay
from shimpaku.api.app import create_app
from shimpaku.snapshot_cli import collect, leaks, scrub


@pytest.fixture()
def snap_file(tmp_path):
    src = TestClient(create_app(demo, auth_mode="off"))

    def get(path, **params):
        if path == "/api/audit/verify":  # demo has no chain; the real export requires ok
            return {"ok": True, "rows": 4}
        r = src.get(path, params=params)
        assert r.status_code == 200, (path, r.text)
        return r.json()

    snap = collect(get)
    p = tmp_path / "snap.json"
    p.write_text(json.dumps(scrub(snap, ["sw1-secret-host"]), default=str))
    replay.load(p)
    return p


@pytest.fixture()
def client(snap_file):
    return TestClient(create_app(replay, auth_mode="off"))


def test_reads_come_from_the_snapshot(client):
    inv = client.get("/api/inventory").json()
    assert inv["replay"] is True and {d["name"] for d in inv["devices"]} >= {"sw1", "leaf1"}
    assert all("mgmt_ip" not in d for d in inv["devices"])
    assert client.get("/api/sle", params={"site": "fabric", "window": 1440}).status_code == 200
    a = client.get("/api/actions").json()["actions"][0]
    assert client.get(f"/api/actions/{a['id']}").json()["id"] == a["id"]
    assert client.get("/api/audit/verify").json()["ok"] is True
    assert client.get("/api/actions/nope").status_code == 404


def test_visitor_is_a_reader_who_cannot_write(client):
    who = client.get("/api/whoami").json()
    assert who["replay"]["taken_at"] and not who["can_approve"] and not who["can_propose"]
    a = client.get("/api/actions").json()["actions"][0]["id"]
    csrf = client.cookies.get("shimpaku_csrf")
    h = {"x-csrf-token": csrf}
    for path in (f"/api/actions/{a}/approve", f"/api/actions/{a}/reject", f"/api/actions/{a}/cancel"):
        assert client.post(path, headers=h).status_code == 403, path
    assert client.post("/api/actions", json={"kind": "bounce_port", "args": {}, "reason": "x"},
                       headers=h).status_code == 403
    assert client.post("/api/tokens", json={"name": "x", "scope": "read"}, headers=h).status_code == 403


def test_bearer_tokens_grant_nothing(client):
    r = client.post("/api/actions", json={"kind": "bounce_port", "args": {}, "reason": "x"},
                    headers={"authorization": "Bearer anything"})
    assert r.status_code == 403


def test_scrub_and_leak_check():
    out = scrub({"mgmt_ip": "10.0.0.1", "log": "/home/alice/x on LabHost", "n": [{"h": "labhost"}]}, ["labhost"])
    assert out == {"log": "~/x on <redacted>", "n": [{"h": "<redacted>"}]}
    assert leaks(json.dumps(out), ["labhost"]) == []
    assert leaks('{"x": "LABHOST"}', ["labhost"]) == ["labhost"]


def test_hardware_twin_comes_from_the_snapshot(client):
    """The replay serves the Hardware page as a visitor saw it at export: facts and ports, no host block."""
    r = client.get("/api/hardware")
    assert r.status_code == 200
    d = r.json()
    assert d["replay"] is True and len(d["devices"]) >= 7
    assert d["hypervisor"].get("host") is None
    assert any(x.get("version") or x.get("uptime_s") for x in d["devices"])
    assert "10.99." not in r.text and "mgmt_ip" not in r.text


def test_hardware_twin_degrades_on_an_older_snapshot(snap_file):
    """A snapshot from before the hardware export: the twin still renders, saying so, with no host block."""
    snap = json.loads(snap_file.read_text())
    snap.pop("hardware", None)
    snap_file.write_text(json.dumps(snap))
    replay.load(snap_file)
    r = TestClient(create_app(replay, auth_mode="off")).get("/api/hardware")
    assert r.status_code == 200
    d = r.json()
    assert d["hypervisor"]["host"] is None and d["hypervisor"]["guests"] == {}
    for x in d["devices"]:
        assert x["uptime_s"] is None and x["counters_reason"]
    assert "10.99." not in r.text and "mgmt_ip" not in r.text
