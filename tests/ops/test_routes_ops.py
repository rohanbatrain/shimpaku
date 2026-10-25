"""/api/ops/* is operator-only and never carries a secret."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "actions"))
from test_engine import box  # noqa: E402,F401
from test_tester_passes import client, invite, operator, redeem, world  # noqa: E402,F401

from shimpaku import hostmon  # noqa: E402
from shimpaku.api import demo  # noqa: E402
from shimpaku.api.app import create_app  # noqa: E402
from shimpaku.gateway import GatewayClient  # noqa: E402

ADMIN = "sk-admin-NEVER-IN-A-RESPONSE"
PATHS = ["/api/ops/host", "/api/ops/gateway"]


def gw_transport():
    def h(req):
        if req.url.path == "/spend/logs/v2":
            return httpx.Response(200, json={"data": [{"api_key": "sk-full-key-value-zz99", "model": "m", "spend": 0.1,
                                                       "status": "success", "metadata": {}}], "total": 1})
        return httpx.Response(200, json={"status": "healthy"})
    return httpx.MockTransport(h)


@pytest.fixture
def ops(world):  # noqa: F811
    st = hostmon.HostStore()
    st.add({"t": 1e12, "temp_c": 93.0, "load": [1, 1, 1]})
    world.app.state.hostmon = st
    world.app.state.gateway = GatewayClient("https://gw.example", ADMIN, transport=gw_transport())
    return world


@pytest.mark.parametrize("path", PATHS)
def test_operator_reads_without_secrets(ops, path):
    c, _ = operator(ops)
    r = c.get(path)
    assert r.status_code == 200, r.text
    blob = r.text
    assert ADMIN not in blob and "sk-full-key-value" not in blob


def test_gateway_masks_and_host_has_shape(ops):
    c, _ = operator(ops)
    g = c.get("/api/ops/gateway").json()
    assert g["keys"][0]["name"] == "…zz99" and g["base"] == "https://gw.example"
    h = c.get("/api/ops/host").json()
    assert h["warn_c"] == 90.0 and h["sampler"] is True and h["guard"]["threshold_c"] == 97.0


@pytest.mark.parametrize("path", PATHS)
def test_signed_out_is_refused(ops, path):
    assert client(ops).get(path).status_code == 401


@pytest.mark.parametrize("path", PATHS)
def test_tester_is_refused(ops, path):
    c, r = redeem(ops, invite(ops))
    assert r.status_code == 200
    assert c.get(path).status_code == 403


@pytest.mark.parametrize("path", PATHS)
def test_bearer_token_is_refused(ops, path):
    tok = ops.auth.create_token("agent", "propose", "admin")["token"]
    assert client(ops).get(path, headers={"Authorization": f"Bearer {tok}"}).status_code == 403


@pytest.mark.parametrize("path", PATHS)
def test_visitor_is_refused_on_public_view(ops, path):
    app = create_app(demo, auth_mode="local", web_dist=ops.tmp / "dist", leases=ops.lm, public_view=True, totp=ops.totp)
    assert TestClient(app).get(path).status_code == 403


def test_demo_payloads(tmp_path):
    app = create_app(demo, auth_mode="off", web_dist=tmp_path, demo=True)
    c = TestClient(app)
    h = c.get("/api/ops/host").json()
    assert h["demo"] and len(h["series"]) == 288 and h["latest"]["temp_c"] >= 90
    g = c.get("/api/ops/gateway").json()
    assert g["demo"] and g["connected"] and "sk-" not in json.dumps(g)
