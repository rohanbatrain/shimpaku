"""Public view (SHIMPAKU_PUBLIC_VIEW) and live-session requests. Fakes only: no lab, no gateway."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "actions"))
from test_engine import box  # noqa: E402,F401
from test_tester_passes import PW, csrf, otp, world  # noqa: E402,F401

from shimpaku import service  # noqa: E402
from shimpaku.actions import fixture  # noqa: E402
from shimpaku.api.app import create_app  # noqa: E402
from shimpaku.api.ratelimit import RateLimiter  # noqa: E402
from shimpaku.api.requests import ics  # noqa: E402

NAME, CONTACT, MSG = "Asha Rao", "asha@example.org", "Saw the post; keen to try the VLAN demo."
HOUR = 3600


def app_for(w, *, public=True, table=None):
    """A second app on the world's lease manager (shared state.db) with public view switched."""
    lim = RateLimiter(table or {"write": (1000, 1000), "read": (1000, 1000), "request": (1000, 1000),
                               "redeem": (1000, 1000)}, clock=w.clock)
    return create_app(service, auth_mode="local", web_dist=w.tmp / "dist", leases=w.lm, limiter=lim,
                      public_view=public, totp=w.totp)


def cl(app) -> TestClient:
    return TestClient(app, base_url="http://testserver")


@pytest.fixture
def pv(world):  # noqa: F811
    world.pv = app_for(world)
    return world


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone(timedelta(hours=5, minutes=30))).isoformat()


def body(w, **kw):
    b = {"name": NAME, "contact": CONTACT, "message": MSG, "consent": True,
         "slots": [iso(w.clock.t + 2 * HOUR), iso(w.clock.t + 26 * HOUR)]}
    b.update(kw)
    return b


def send(w, c=None, **kw):
    c = c or cl(w.pv)
    return c.post("/api/session-requests", json=body(w, **kw), headers=csrf(c))


def op(w):
    """One signed-in operator client per world (sign-in is limited to 5 a minute per address)."""
    if getattr(w, "_op", None) is None or w._op[2] is not w.pv:
        c = cl(w.pv)
        assert c.post("/api/login", json={"password": PW, "code": otp(w)}, headers=csrf(c)).status_code == 200
        w._op = (c, csrf(c), w.pv)
    return w._op[0], w._op[1]


# ================================================================ viewer

READS = ["/api/inventory", "/api/sle", "/api/incidents", "/api/actions", "/api/audit", "/api/topology",
         "/api/bench/results", "/api/lease", "/api/settings"]


def test_visitor_reads_without_signing_in(pv, monkeypatch):
    async def inv(site=None):
        return {"devices": [{"name": "sw1", "mgmt_ip": "10.9.9.9"}], "sites": []}
    monkeypatch.setattr(service, "inventory_list", inv, raising=False)
    c = cl(pv.pv)
    w = c.get("/api/whoami").json()
    assert w["authenticated"] and w["role"] == "visitor" and w["public_view"] is True
    assert not w["can_propose"] and not w["can_approve"]
    for path in READS:
        assert c.get(path).status_code not in (401, 403), path
    out = c.get("/api/inventory").json()
    assert out["devices"] and all("mgmt_ip" not in d for d in out["devices"])
    assert "10.9.9.9" not in json.dumps(out)


def test_visitor_sse_streams(pv):
    c = cl(pv.pv)
    with c.stream("GET", "/api/stream/actions") as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/actions"), ("POST", "/api/actions/act_x/approve"), ("POST", "/api/actions/act_x/reject"),
    ("POST", "/api/actions/act_x/cancel"), ("POST", "/api/demo/break"), ("POST", "/api/demo/seed"),
    ("POST", "/api/lease/token"), ("POST", "/api/lease/end"), ("GET", "/api/tokens"), ("POST", "/api/tokens"),
    ("GET", "/api/admin/invites"), ("POST", "/api/admin/invites"), ("GET", "/api/admin/leases"),
    ("POST", "/api/admin/purge"), ("GET", "/api/admin/session-requests"),
    ("GET", "/api/admin/session-requests/req_x"), ("GET", "/api/admin/session-requests/req_x.ics"),
    ("POST", "/api/admin/session-requests/req_x/approve"), ("POST", "/api/admin/session-requests/req_x/decline"),
])
def test_visitor_never_writes_or_reaches_operator_routes(pv, method, path):
    c = cl(pv.pv)
    kw = {"json": {"kind": fixture.KIND, "args": fixture.ARGS, "reason": "x", "name": "x", "scope": "read",
                   "slot_start": iso(pv.clock.t + 3 * HOUR)}} if method == "POST" else {}
    # with the CSRF pair (a real browser) and without (a script)
    assert c.request(method, path, headers=csrf(c), **kw).status_code == 403, path
    assert cl(pv.pv).request(method, path, **kw).status_code in (401, 403), path
    assert pv.eng.store.count_open() == 0


def test_visitor_cannot_ask_shim(pv, monkeypatch):
    called = []

    async def fake(q, who, ctx, **kw):
        called.append(q)
        yield {"type": "done"}
    monkeypatch.setattr(service, "assistant_ask", fake, raising=False)
    app = app_for(pv)
    c = cl(app)
    r = c.post("/api/assistant/ask", json={"question": "hi"}, headers=csrf(c))
    assert r.status_code == 403 and "Request a live session" in r.json()["detail"]
    assert called == []


def test_visitor_reads_are_rate_limited_per_address_and_not_audited(pv, caplog):
    app = app_for(pv, table={"read": (3, 1000)})
    c = cl(app)
    before = len(pv.audit.query(limit=1000))
    import logging
    with caplog.at_level(logging.INFO, logger="shimpaku.api"):
        codes = [c.get("/api/actions").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    assert len(pv.audit.query(limit=1000)) == before  # no audit row per anonymous read
    assert "testclient" not in caplog.text and "visitor GET /api/actions" in caplog.text
    # the operator is not held to the visitor bucket
    o = cl(app)
    o.post("/api/login", json={"password": PW, "code": otp(pv)}, headers=csrf(o))
    assert o.get("/api/actions").status_code == 200


def test_public_view_off_keeps_401(world):  # noqa: F811
    c = cl(app_for(world, public=False))
    assert c.get("/api/whoami").json()["authenticated"] is False
    for path in ("/api/actions", "/api/inventory", "/api/stream/actions"):
        assert c.get(path).status_code == 401


def test_public_view_never_applies_in_off_mode(world, monkeypatch):  # noqa: F811
    app = create_app(service, auth_mode="off", web_dist=world.tmp / "dist", leases=world.lm, public_view=True)
    assert cl(app).get("/api/whoami").json()["role"] == "operator"
    assert app.state.public_view is False


def test_mcp_still_needs_a_token_with_public_view(pv, monkeypatch):
    from shimpaku.mcp.server import McpHttpGate

    async def inner(scope, receive, send):  # pragma: no cover - must not be reached
        raise AssertionError("reached the MCP app without a token")
    gate = McpHttpGate(inner, pv.lm.verify_token, pv.pv.state.limiter, auth_mode="local")
    r = TestClient(gate).post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Bearer")


# ================================================================ requests: validation

def test_request_round_trip_and_status_capability(pv):
    r = send(pv)
    assert r.status_code == 201, r.text
    s = r.json()["status_token"]
    assert len(s) >= 43 and r.json()["status_path"] == f"/request/status?s={s}"
    st = cl(pv.pv).get(f"/api/session-requests/status?s={s}")
    assert st.status_code == 200 and st.json()["status"] == "pending" and st.json()["link"] is None
    assert NAME not in st.text and CONTACT not in st.text  # the status page shows no identity
    for bad in ("", "x", s[:-1] + ("A" if s[-1] != "A" else "B"), "req_" + "0" * 12, "a" * 300):
        assert cl(pv.pv).get("/api/session-requests/status", params={"s": bad}).status_code == 404
    assert st.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize("patch,code", [
    ({"consent": False}, 422),
    ({"name": ""}, 422), ({"name": "x" * 81}, 422),
    ({"contact": "not a contact"}, 422), ({"contact": "https://evil.example/in/x"}, 422),
    ({"contact": "javascript:alert(1)@x.yz"}, 201),  # loose e-mail shape; stored as text, never a link
    ({"contact": "https://www.linkedin.com/in/asha-rao"}, 201), ({"contact": "linkedin.com/in/asha"}, 201),
    ({"message": "m" * 501}, 422),
    ({"slots": []}, 422),
    ({"slots": ["2030-01-01T10:00:00"]}, 422),  # no timezone
    ({"slots": "4 slots"}, 422),
    ("four", 422),
    ("past", 422), ("soon", 422), ("far", 422),
])
def test_request_validation(pv, patch, code):
    t = pv.clock.t
    if patch == "four":
        patch = {"slots": [iso(t + (2 + i) * HOUR) for i in range(4)]}
    elif patch == "past":
        patch = {"slots": [iso(t - HOUR)]}
    elif patch == "soon":
        patch = {"slots": [iso(t + 50 * 60)]}
    elif patch == "far":
        patch = {"slots": [iso(t + 31 * 86400)]}
    assert send(pv, **patch).status_code == code


def test_request_needs_csrf_and_same_origin(pv):
    c = cl(pv.pv)
    assert c.post("/api/session-requests", json=body(pv)).status_code == 403
    h = csrf(c)
    assert c.post("/api/session-requests", json=body(pv), headers={**h, "Origin": "https://evil.example"}).status_code == 403
    assert c.post("/api/session-requests", json=body(pv), headers=h).status_code == 201


def test_request_is_rate_limited_per_address(pv):
    app = app_for(pv, table={"request": (2, 100)})
    c = cl(app)
    codes = [c.post("/api/session-requests", json=body(pv), headers=csrf(c)).status_code for _ in range(3)]
    assert codes == [201, 201, 429]


def test_request_works_with_public_view_off(world):  # noqa: F811
    world.pv = app_for(world, public=False)
    assert send(world).status_code == 201


def test_pending_cap(pv, monkeypatch):
    from shimpaku.config import settings
    monkeypatch.setattr(settings, "session_requests_max_pending", 2)
    assert [send(pv).status_code for _ in range(3)] == [201, 201, 503]


# ================================================================ approve -> invite in the window

def approve(w, rid, start):
    c, h = op(w)
    return c.post(f"/api/admin/session-requests/{rid}/approve", json={"slot_start": iso(start)}, headers=h)


def a_request(w):
    s = send(w).json()["status_token"]
    c, _ = op(w)
    rid = c.get("/api/admin/session-requests").json()["requests"][0]["id"]
    return rid, s


def redeem(w, token):
    c = cl(w.pv)
    return c, c.post("/api/invites/redeem", json={"token": token, "consent": True}, headers=csrf(c))


def test_operator_lists_with_identity_and_pending_count(pv):
    rid, _ = a_request(pv)
    c, _ = op(pv)
    out = c.get("/api/admin/session-requests").json()
    assert out["pending"] == 1 and out["requests"][0]["name"] == NAME and out["requests"][0]["contact"] == CONTACT
    assert c.get(f"/api/admin/session-requests/{rid}").json()["message"] == MSG
    # identity never reaches the audit chain
    assert NAME not in json.dumps(pv.audit.query(limit=1000)) and CONTACT not in json.dumps(pv.audit.query(limit=1000))


def test_approved_invite_redeems_only_inside_its_window(pv):
    rid, s = a_request(pv)
    start = pv.clock.t + 5 * HOUR  # differs from both requested slots
    r = approve(pv, rid, start)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "approved" and out["slot_start"] == start and out["slot_end"] == start + 1800
    assert out["link"].startswith("https://demo.example/start?i=")
    tok = out["link"].split("i=")[1]
    st = cl(pv.pv).get(f"/api/session-requests/status?s={s}").json()
    assert st["status"] == "approved" and st["link"] == out["link"] and st["opens_at"] == start - 600
    # too early: 11 minutes before
    pv.clock.t = start - 11 * 60
    assert cl(pv.pv).get(f"/api/invites/status?i={tok}").json()["reason"] == "early"
    _, r = redeem(pv, tok)
    assert r.status_code == 425 and "not opened" in r.json()["detail"]
    # 9 minutes before: open; the lease ends at min(start of lease + 30 min, slot end + 5 min)
    pv.clock.t = start - 9 * 60
    c, r = redeem(pv, tok)
    assert r.status_code == 200, r.text
    lease = r.json()["lease"]
    assert lease["state"] == "active" and lease["ends"] == pv.clock.t + 1800
    assert cl(pv.pv).get(f"/api/session-requests/status?s={s}").json()["status"] == "used"
    assert cl(pv.pv).get(f"/api/session-requests/status?s={s}").json()["link"] is None


def test_late_redemption_lease_is_capped_at_slot_end_plus_five(pv):
    rid, _ = a_request(pv)
    start = pv.clock.t + 3 * HOUR
    tok = approve(pv, rid, start).json()["link"].split("i=")[1]
    pv.clock.t = start + 20 * 60
    _, r = redeem(pv, tok)
    assert r.status_code == 200 and r.json()["lease"]["ends"] == start + 1800 + 300


def test_invite_closes_at_slot_end_and_request_expires(pv):
    rid, s = a_request(pv)
    start = pv.clock.t + 3 * HOUR
    tok = approve(pv, rid, start).json()["link"].split("i=")[1]
    pv.clock.t = start + 1800
    _, r = redeem(pv, tok)
    assert r.status_code == 410
    st = cl(pv.pv).get(f"/api/session-requests/status?s={s}").json()
    assert st["status"] == "expired" and st["link"] is None


def test_only_pending_requests_are_approved(pv):
    rid, _ = a_request(pv)
    assert approve(pv, rid, pv.clock.t + 3 * HOUR).status_code == 200
    assert approve(pv, rid, pv.clock.t + 4 * HOUR).status_code == 409
    assert approve(pv, "req_nope", pv.clock.t + 4 * HOUR).status_code == 404
    rid2, _ = a_request(pv)
    assert approve(pv, rid2, pv.clock.t - 2 * HOUR).status_code == 422  # slot already over


def test_pending_request_expires_after_its_slots(pv):
    _, s = a_request(pv)
    pv.clock.t += 27 * HOUR
    assert cl(pv.pv).get(f"/api/session-requests/status?s={s}").json()["status"] == "expired"


def test_decline_with_note_and_withdraw_an_approval(pv):
    rid, s = a_request(pv)
    c, h = op(pv)
    r = c.post(f"/api/admin/session-requests/{rid}/decline", json={"note": "Fully booked this month."}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "declined"
    st = cl(pv.pv).get(f"/api/session-requests/status?s={s}").json()
    assert st["status"] == "declined" and st["decline_note"] == "Fully booked this month."
    assert c.post(f"/api/admin/session-requests/{rid}/decline", json={}, headers=h).status_code == 409
    # an approved one can be withdrawn: the invite dies with it
    rid2, s2 = a_request(pv)
    tok = approve(pv, rid2, pv.clock.t + 2 * HOUR).json()["link"].split("i=")[1]
    assert c.post(f"/api/admin/session-requests/{rid2}/decline", json={}, headers=h).status_code == 200
    pv.clock.t += 2 * HOUR
    assert redeem(pv, tok)[1].status_code == 410
    assert cl(pv.pv).get(f"/api/session-requests/status?s={s2}").json()["link"] is None


# ================================================================ calendar

def test_ics_for_operator_and_requester_does_not_consume(pv):
    rid, s = a_request(pv)
    start = pv.clock.t + 3 * HOUR
    out = approve(pv, rid, start).json()
    tok = out["link"].split("i=")[1]
    c, _ = op(pv)
    a = c.get(f"/api/admin/session-requests/{rid}.ics")
    st = cl(pv.pv).get(f"/api/session-requests/status?s={s}").json()
    b = cl(pv.pv).get(st["ics_url"])
    for r in (a, b):
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
        assert "attachment" in r.headers["content-disposition"]
        text = r.text
        assert text.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n") and text.endswith("END:VCALENDAR\r\n")
        unfolded = text.replace("\r\n ", "")
        assert f"UID:{rid}@shimpaku\r\n" in unfolded and "DTSTAMP:" in unfolded
        assert f"DTSTART:{datetime.fromtimestamp(start, timezone.utc):%Y%m%dT%H%M%SZ}\r\n" in unfolded
        assert f"DTEND:{datetime.fromtimestamp(start + 1800, timezone.utc):%Y%m%dT%H%M%SZ}\r\n" in unfolded
        assert "SUMMARY:Shimpaku live session" in unfolded and out["link"] in unfolded
        assert "ATTENDEE" not in text and CONTACT not in text and NAME not in text
        assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))
    assert cl(pv.pv).get("/api/invites/ics?i=nope").status_code == 404
    pv.clock.t = start
    assert redeem(pv, tok)[1].status_code == 200  # the .ics fetches did not use it up
    assert cl(pv.pv).get("/api/admin/session-requests/req_x.ics").status_code in (401, 403)


def test_ics_escapes_and_folds():
    text = ics("req_1", 0, 1800, "https://x.example/start?i=" + "a" * 120 + ";b,c", 0)
    assert "\\;b\\,c" in text.replace("\r\n ", "")
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))


# ================================================================ retention and roles

def test_purge_removes_request_identity_90_days_after_the_slot_or_decline(pv):
    rid, _ = a_request(pv)
    start = pv.clock.t + 3 * HOUR
    approve(pv, rid, start)
    rid2, _ = a_request(pv)
    c, h = op(pv)
    c.post(f"/api/admin/session-requests/{rid2}/decline", json={}, headers=h)
    assert pv.lm.purge()["requests"] == 0
    pv.clock.t = start + 1800 + 90 * 86400 - 10
    assert pv.lm.purge()["requests"] == 1  # the declined one (decided 3 h earlier)
    pv.clock.t += 20
    assert pv.lm.purge()["requests"] == 1  # the approved slot, 90 days after it ended
    rows = c.get("/api/admin/session-requests").json()["requests"]
    assert all(r["name"] is None and r["contact"] is None and r["message"] is None and r["purged"] for r in rows)


def test_tester_cannot_reach_request_admin(pv):
    rid, _ = a_request(pv)
    tok = approve(pv, rid, pv.clock.t + 2 * HOUR).json()["link"].split("i=")[1]
    pv.clock.t += 2 * HOUR
    t, r = redeem(pv, tok)
    assert r.status_code == 200
    h = csrf(t)
    assert t.get("/api/admin/session-requests", headers=h).status_code == 403
    assert t.post(f"/api/admin/session-requests/{rid}/decline", json={}, headers=h).status_code == 403
    assert cl(app_for(pv, public=False)).get("/api/admin/session-requests").status_code == 401


def test_cli_requests_list_approve_decline(pv, monkeypatch):
    from typer.testing import CliRunner

    from shimpaku.api import cli_admin

    monkeypatch.setattr(cli_admin, "_leases", lambda: pv.lm)
    rid, _ = a_request(pv)
    rid2, _ = a_request(pv)
    run = CliRunner()
    out = run.invoke(cli_admin.admin_app, ["requests", "list"])
    assert out.exit_code == 0 and rid in out.output and NAME in out.output
    out = run.invoke(cli_admin.admin_app, ["requests", "approve", rid, "--choice", "2"])
    assert out.exit_code == 0, out.output
    assert "https://demo.example/start?i=" in out.output and "/api/invites/ics?i=" in out.output
    assert pv.lm.requests.get(rid)["slot_start"] == pv.lm.requests.get(rid)["slots"][1]
    out = run.invoke(cli_admin.admin_app, ["requests", "approve", rid2, "--slot", "2030-01-01T10:00"])
    assert out.exit_code == 2
    out = run.invoke(cli_admin.admin_app, ["requests", "decline", rid2, "--note", "sorry"])
    assert out.exit_code == 0 and pv.lm.requests.get(rid2)["status"] == "declined"


def test_requests_closed_without_a_privacy_contact(pv, monkeypatch):
    from shimpaku.config import settings
    monkeypatch.setattr(settings, "privacy_contact", None)
    r = send(pv)
    assert r.status_code == 503 and "privacy contact" in r.json()["detail"]
