"""Red team of the presenter role and the Junos CLI console (``/api/console/*``), in-process with
a fake device runner. No lab.

Each test is an attack; it passes when the attack fails. On the code before the console existed
the access tests fail (no route: 404 instead of 401/403 is asserted as a refusal of the specific
kind), and the policy tests fail on import.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT / "actions"))
from test_engine import box  # noqa: E402,F401
from test_tester_passes import client, csrf, invite, operator, redeem, world  # noqa: E402,F401

from shimpaku import console_policy  # noqa: E402

DEVICES = ["leaf1", "sw1"]


class FakeRunner:
    def __init__(self, output: str = "Interface  Admin Link\nge-0/0/0  up    up\n"):
        self.output = output
        self.calls: list[tuple[str, str]] = []
        self.raise_: Exception | None = None

    def devices(self):
        return list(DEVICES)

    def run(self, device, command, timeout):
        self.calls.append((device, command))
        if self.raise_:
            raise self.raise_
        return self.output


@pytest.fixture
def fake(world):  # noqa: F811
    r = FakeRunner()
    world.app.state.console_runner = r
    return r


def _exec(c, h, command="show interfaces terse", device="sw1"):
    return c.post("/api/console/exec", json={"device": device, "command": command}, headers=h)


def _console_rows(w) -> list[dict]:
    return [r for r in w.audit.query(limit=1000) if r.get("tool") == "console_exec"]


# ================================================================ who may use it

def test_operator_runs_a_show_command(world, fake):
    c, h = operator(world)
    r = _exec(c, h)
    assert r.status_code == 200, r.text
    assert r.json()["output"].startswith("Interface") and fake.calls == [("sw1", "show interfaces terse")]


def test_anonymous_gets_401_on_every_console_endpoint(world, fake):
    c = client(world)
    h = csrf(c)
    assert c.get("/api/console/devices").status_code == 401
    assert _exec(c, h).status_code == 401
    assert c.post("/api/console/presenting", json={"on": True}, headers=h).status_code == 401
    assert fake.calls == []


def test_public_visitor_gets_403(world, fake):
    world.app.state.public_view = True  # informational; the visitor principal comes from create_app
    from shimpaku.api.app import create_app
    from shimpaku import service
    from shimpaku.api.ratelimit import RateLimiter
    app = create_app(service, auth_mode="local", web_dist=world.tmp / "dist", leases=world.lm,
                     limiter=RateLimiter({}), public_view=True, totp=world.totp)
    app.state.console_runner = fake
    from fastapi.testclient import TestClient
    c = TestClient(app)
    c.get("/api/whoami")
    h = {"X-CSRF-Token": c.cookies.get("shimpaku_csrf"), "Origin": "http://testserver"}
    assert c.get("/api/whoami").json()["role"] == "visitor"
    assert c.get("/api/console/devices").status_code == 403
    assert _exec(c, h).status_code == 403
    assert c.post("/api/console/presenting", json={"on": True}, headers=h).status_code == 403
    assert fake.calls == []


def test_tester_session_gets_403(world, fake):
    t, r = redeem(world, invite(world))
    assert r.status_code == 200
    h = csrf(t)
    assert t.get("/api/console/devices").status_code == 403
    assert _exec(t, h).status_code == 403
    assert t.post("/api/console/presenting", json={"on": True}, headers=h).status_code == 403
    assert fake.calls == []


@pytest.mark.parametrize("scope", ["read", "propose"])
def test_operator_tokens_get_403_whatever_the_scope(world, fake, scope):
    tok = world.auth.create_token("agent", scope, "admin")["token"]
    c = client(world)
    b = {"Authorization": f"Bearer {tok}"}
    assert c.get("/api/console/devices", headers=b).status_code == 403
    assert _exec(c, b).status_code == 403
    assert c.post("/api/console/presenting", json={"on": True}, headers=b).status_code == 403
    assert fake.calls == []


def test_a_token_minted_with_an_operator_role_is_still_not_a_presenter(world, fake):
    tok = world.auth.create_token("forged", "propose", "admin", role="operator")["token"]
    c = client(world)
    b = {"Authorization": f"Bearer {tok}"}
    assert _exec(c, b).status_code == 403
    assert fake.calls == []


def test_tester_lease_token_gets_403(world, fake):
    t, r = redeem(world, invite(world))
    tok = t.post("/api/lease/token", headers=csrf(t)).json()["token"]
    assert _exec(client(world), {"Authorization": f"Bearer {tok}"}).status_code == 403
    assert fake.calls == []


def test_cross_site_post_refused(world, fake):
    c, h = operator(world)
    r = _exec(c, {**h, "Origin": "https://evil.example"})
    assert r.status_code == 403 and fake.calls == []


# ================================================================ presenter mode

def test_presenter_mode_flag_on_the_operators_session(world, fake):
    c, h = operator(world)
    assert c.get("/api/whoami").json()["presenting"] is False
    assert c.post("/api/console/presenting", json={"on": True}, headers=h).json()["presenting"] is True
    w = c.get("/api/whoami").json()
    assert w["presenting"] is True and w["role"] == "operator"
    assert _exec(c, h).status_code == 200
    row = _console_rows(world)[-1]
    assert row["actor"]["role"] == "presenter"
    c.post("/api/console/presenting", json={"on": False}, headers=h)
    assert c.get("/api/whoami").json()["presenting"] is False


def test_tester_session_row_cannot_be_flagged_presenting(world):
    t, r = redeem(world, invite(world))
    from shimpaku.api.app import _cookie_names
    sid = t.cookies.get(_cookie_names(False)[0])
    assert world.auth.set_presenting(sid, True) is False
    assert t.get("/api/whoami").json()["presenting"] is False


# ================================================================ command policy over HTTP

ATTACKS = [
    "conf", "configure", "configure private", "req system reboot", "request system reboot", "start sh",
    "start shell", "file delete /var/tmp/x", "show version | save /tmp/x", "show version | s /tmp/x",
    "show ver; request system reboot", "show version\nrequest system reboot", "ｓhow version",
    "show version request system reboot", "request support information", "clear arp", "restart routing",
    "set cli idle-timeout 0", "delete x", "commit", "rollback 1", "load merge x", "op x", "edit",
    "monitor interface ge-0/0/0", "show configuration", "ping 10.1.0.1 count 50", "save x", "file copy a b",
]


@pytest.mark.parametrize("cmd", ATTACKS)
def test_refused_command_never_reaches_the_device_and_is_audited(world, fake, cmd):
    c, h = operator(world)
    r = _exec(c, h, cmd)
    assert r.status_code == 422, (cmd, r.status_code, r.text)
    assert r.json()["detail"].startswith("refused")
    assert fake.calls == []
    row = _console_rows(world)[-1]
    assert row["status"] == "refused" and row["args"]["outcome"] == "refused"


def test_unknown_device_refused(world, fake):
    c, h = operator(world)
    assert _exec(c, h, device="r9").status_code == 404
    assert _exec(c, h, device="../etc").status_code == 422
    assert fake.calls == []


def test_ping_gets_a_count(world, fake):
    c, h = operator(world)
    assert _exec(c, h, "ping 10.1.0.1").status_code == 200
    assert fake.calls[-1] == ("sw1", "ping 10.1.0.1 count 5")


# ================================================================ output, audit, limits

def test_output_is_capped(world, fake):
    fake.output = "x" * (console_policy.MAX_OUTPUT * 3)
    c, h = operator(world)
    j = _exec(c, h).json()
    assert j["truncated"] is True and len(j["output"].encode()) == console_policy.MAX_OUTPUT


def test_audit_row_has_command_device_outcome_duration_and_no_output(world, fake):
    secret = "UNIQUE-OUTPUT-MARKER-7f3a"
    fake.output = f"line one\n{secret}\n"
    c, h = operator(world)
    assert _exec(c, h, "show version").status_code == 200
    row = _console_rows(world)[-1]
    a = row["args"]
    assert a["device"] == "sw1" and a["command"] == "show version" and a["sent"] == "show version"
    assert a["outcome"] == "ok" and isinstance(a["duration_ms"], int) and a["output_len"] == len(fake.output)
    assert row["actor"]["role"] == "operator" and row["actor"]["name"] == "admin"
    assert secret not in json.dumps(row) and secret not in (world.tmp / "audit.jsonl").read_text()
    assert world.audit.verify()["ok"]


def test_device_error_is_audited_without_detail(world, fake):
    fake.raise_ = ConnectionRefusedError("10.99.0.13:830 refused")
    c, h = operator(world)
    r = _exec(c, h)
    assert r.status_code == 502 and "10.99" not in r.text
    assert _console_rows(world)[-1]["status"] == "error:ConnectionRefusedError"


def test_timeout(world, fake, monkeypatch):
    import time as _t
    monkeypatch.setattr(console_policy, "TIMEOUT_S", 0.2)
    fake.run = lambda d, cmd, t: (_t.sleep(1), "late")[1]
    c, h = operator(world)
    r = _exec(c, h)
    assert r.status_code == 504
    assert _console_rows(world)[-1]["status"] == "error:timeout"


def test_rate_limited_per_principal(world, fake):
    c, h = operator(world)
    codes = [_exec(c, h).status_code for _ in range(25)]
    assert codes[:20] == [200] * 20 and 429 in codes[20:]
    r = _exec(c, h)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
