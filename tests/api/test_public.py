"""routes_public: the probe heatmap (analytics/heatmap.py) and the landing page's public summary
(CORS allow-list, no sign-in, no secrets, honest unknowns)."""

from __future__ import annotations

import json
import re

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from shimpaku.analytics import heatmap
from shimpaku.api import demo, replay
from shimpaku.api.app import create_app
from shimpaku.api.routes_public import unsafe_executed
from shimpaku.config import settings
from shimpaku.snapshot_cli import collect, scrub

IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


# ---- heatmap ---------------------------------------------------------------------------------
def _probes(end):
    rows = []
    for m in range(1, 31):
        t = end - pd.Timedelta(minutes=m)
        down = 10 <= m <= 12
        rows.append(dict(ts=t, client_id="rpm:r1:shimpaku/to-r2", target="10.0.12.2", kind="rpm-icmp", ok=not down,
                         rtt_ms=None if down else 3.0, jitter_ms=None if down else 0.5, loss_pct=100.0 if down else 0.0))
        rows.append(dict(ts=t, client_id="rpm:leaf1:shimpaku/to-leaf2", target="10.1.0.2", kind="rpm-icmp", ok=True,
                         rtt_ms=4.0, jitter_ms=0.4, loss_pct=0.0))
    return pd.DataFrame(rows)


def test_heatmap_cells_carry_values_n_and_fault_minutes():
    end = pd.Timestamp("2026-10-25 12:00")
    h = heatmap.build(_probes(end), end - pd.Timedelta(minutes=60), end, "campus", 60)
    assert h["bucket_min"] == 1 and len(h["columns"]) == 60
    assert [p["label"] for p in h["paths"]] == ["r1 → to-r2"]  # site filter: leaf1 is fabric
    cells = [c for c in h["paths"][0]["cells"] if c]
    assert len(cells) == 30 and all(c["n"] == 1 for c in cells)
    bad = [c for c in cells if c["bad_minutes"]]
    assert len(bad) == 3 and all(c["loss_pct"] == 100.0 and c["classifier"] == "Unreachable" for c in bad)
    good = [c for c in cells if not c["bad_minutes"]]
    assert all(c["rtt_ms"] == 3.0 and c["jitter_ms"] == 0.5 and c["minutes"] == 1 for c in good)
    assert not IP.search(json.dumps(h))  # targets never leave


def test_heatmap_buckets_long_windows():
    assert heatmap.bucket_for(60) == 1 and heatmap.bucket_for(1440) == 15 and heatmap.bucket_for(10080) == 120
    end = pd.Timestamp("2026-10-25 12:00")
    h = heatmap.build(_probes(end), end - pd.Timedelta(minutes=1440), end, "campus", 1440)
    assert len(h["columns"]) <= 97
    cells = [c for c in h["paths"][0]["cells"] if c]
    assert sum(c["n"] for c in cells) == 30


def test_heatmap_empty_store_is_empty_not_invented():
    end = pd.Timestamp("2026-10-25 12:00")
    h = heatmap.build(pd.DataFrame(columns=heatmap.COLS), end - pd.Timedelta(minutes=60), end, "campus", 60)
    assert h["paths"] == []


def test_heatmap_route_demo_and_provider_missing():
    c = TestClient(create_app(demo, auth_mode="off"))
    h = c.get("/api/probes/heatmap", params={"site": "fabric", "window": 60}).json()
    assert h["demo"] is True and h["paths"] and any(x and x["bad_minutes"] for p in h["paths"] for x in p["cells"])

    class NoProvider:
        REPLAY = False

        async def probe_heatmap(self, site, window_min=60):
            return {"available": False, "reason": "probe_heatmap: no data source registered yet"}

    r = TestClient(create_app(NoProvider(), auth_mode="off")).get("/api/probes/heatmap").json()
    assert r["available"] is False


def test_heatmap_needs_sign_in(monkeypatch):
    from shimpaku.api.auth import hash_password
    monkeypatch.setenv("SHIMPAKU_ADMIN_PASSWORD_HASH", hash_password("pw-pw-pw-pw"))
    assert TestClient(create_app(demo, auth_mode="local")).get("/api/probes/heatmap").status_code == 401


# ---- public summary --------------------------------------------------------------------------
def test_unsafe_counts_commits_without_an_allowed_policy():
    assert unsafe_executed([{"commits": [{}], "policy": {"allowed": True}}, {"commits": [], "policy": {"allowed": False}},
                            {"status": "blocked", "policy": {"allowed": False}}]) == 0
    assert unsafe_executed([{"commits": [{"ts": 1}], "policy": {"allowed": False}}, {"commits": [{}], "policy": {}}]) == 2


def test_summary_is_public_and_cors_only_for_listed_origins(monkeypatch):
    from shimpaku.api.auth import hash_password
    monkeypatch.setenv("SHIMPAKU_ADMIN_PASSWORD_HASH", hash_password("pw-pw-pw-pw"))
    monkeypatch.setattr(settings, "public_cors_origins", "https://shimpaku.example, https://other.example/")
    c = TestClient(create_app(demo, auth_mode="local"))  # signed out: the summary still answers
    r = c.get("/api/public/summary", headers={"Origin": "https://shimpaku.example"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "https://shimpaku.example"
    assert "access-control-allow-credentials" not in r.headers and "Origin" in r.headers["vary"]
    assert c.get("/api/public/summary", headers={"Origin": "https://other.example"}).headers[
        "access-control-allow-origin"] == "https://other.example"
    evil = c.get("/api/public/summary", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in evil.headers
    assert c.post("/api/public/summary").status_code in (404, 405)
    d = r.json()
    assert d["lab"]["live"] is True and d["devices"]["total"] == 7
    assert d["actions"]["unsafe_executed"] == 0 and d["audit"]["verified"] is True
    assert d["last_action"]["status"] and "at" in d["last_action"]
    assert not IP.search(r.text) and "mgmt_ip" not in r.text and "token" not in r.text.lower()


@pytest.fixture()
def replay_client(tmp_path):
    src = TestClient(create_app(demo, auth_mode="off"))

    def get(path, **params):
        if path == "/api/audit/verify":
            return {"ok": True, "rows": 4}
        r = src.get(path, params=params)
        assert r.status_code == 200, (path, r.text)
        return r.json()

    p = tmp_path / "snap.json"
    p.write_text(json.dumps(scrub(collect(get), []), default=str))
    replay.load(p)
    return TestClient(create_app(replay, auth_mode="off"))


def test_summary_on_replay_says_offline_and_does_not_guess_devices(replay_client):
    d = replay_client.get("/api/public/summary").json()
    assert d["lab"]["live"] is False and d["lab"]["replay_taken_at"]
    assert d["devices"]["up"] is None and "replay" in d["devices"]["basis"]
    assert d["devices"]["total"] == 7 and d["actions"]["unsafe_executed"] == 0
    assert d["audit"]["verified"] is True


def test_replay_serves_the_recorded_heatmap(replay_client):
    h = replay_client.get("/api/probes/heatmap", params={"site": "campus", "window": 60}).json()
    assert h["replay"] is True and h["paths"]


def test_provider_reads_probe_result_from_the_store():
    import asyncio

    import duckdb

    from shimpaku import providers, service
    con = duckdb.connect()
    con.execute("CREATE TABLE probe_result (ts TIMESTAMP, client_id VARCHAR, target VARCHAR, kind VARCHAR, ok BOOLEAN, "
                "rtt_ms DOUBLE, jitter_ms DOUBLE, loss_pct DOUBLE)")
    now = pd.Timestamp.now(tz="UTC").tz_localize(None)
    con.execute("INSERT INTO probe_result VALUES (?, 'rpm:sw1:shimpaku/to-r1', '10.0.0.1', 'rpm-icmp', true, 2.5, 0.3, 0)",
                [now - pd.Timedelta(minutes=2)])
    saved = dict(service._providers)
    try:
        providers.register_all(con)
        h = asyncio.run(service.probe_heatmap("campus", 60))
    finally:
        service._providers.clear()
        service._providers.update(saved)
    assert h["paths"][0]["label"] == "sw1 → to-r1" and h["paths"][0]["n"] == 1
    assert not IP.search(json.dumps(h))
