"""Red team of the public surfaces added with public view and session requests: the anonymous
visitor, the request form, the status capability and the calendar links. Each test is an attack;
it passes when the attack fails. In-process with fakes, no lab."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT / "actions"))
from test_engine import box  # noqa: E402,F401
from test_public_view import (CONTACT, HOUR, NAME, a_request, app_for, approve, body, cl, op,  # noqa: E402
                              pv, redeem, send)
from test_tester_passes import csrf, invite, world  # noqa: E402,F401

from shimpaku.actions import fixture  # noqa: E402

_ = (pv, box, world)


def test_spoofed_forwarding_headers_do_not_reset_the_visitor_read_limit(pv):  # noqa: F811
    app = app_for(pv, table={"read": (2, 1000)})
    c = cl(app)
    codes = [c.get("/api/actions", headers={"X-Forwarded-For": f"203.0.113.{i}", "X-Real-IP": f"198.51.100.{i}"})
             .status_code for i in range(3)]
    assert codes == [200, 200, 429]


def test_reconnecting_sse_is_charged_to_the_visitor_read_limit(pv):  # noqa: F811
    c = cl(app_for(pv, table={"read": (2, 1000)}))
    codes = []
    for _ in range(3):
        with c.stream("GET", "/api/stream/actions") as r:
            codes.append(r.status_code)
    assert codes == [200, 200, 429]


@pytest.mark.parametrize("cookie", ["", "garbage", "x" * 200])
def test_forged_session_cookies_stay_visitors(pv, cookie):  # noqa: F811
    c = cl(pv.pv)
    c.cookies.set("shimpaku_session", cookie)
    w = c.get("/api/whoami").json()
    assert w["role"] == "visitor" and not w["can_propose"] and not w["can_approve"]
    r = c.post("/api/actions", json={"kind": fixture.KIND, "args": fixture.ARGS, "reason": "x"}, headers=csrf(c))
    assert r.status_code == 403 and pv.eng.store.count_open() == 0


def test_a_dead_tester_session_falls_back_to_visitor_not_tester(pv):  # noqa: F811
    c = cl(pv.pv)
    r = c.post("/api/invites/redeem", json={"token": invite(pv), "consent": True}, headers=csrf(c))
    assert r.status_code == 200
    h = csrf(c)
    assert c.post("/api/lease/end", headers=h).status_code == 200
    # the cookie is deleted by the reply; replay the old one
    w = c.get("/api/whoami").json()
    assert w["role"] == "visitor"
    assert c.post("/api/demo/break", headers=csrf(c)).status_code == 403
    assert c.post("/api/lease/token", headers=csrf(c)).status_code == 403


def test_visitor_whoami_sets_no_session(pv):  # noqa: F811
    r = cl(pv.pv).get("/api/whoami")
    assert not any(k.endswith("shimpaku_session") for k in r.cookies.keys())


def test_status_token_of_one_request_reveals_nothing_of_another(pv):  # noqa: F811
    rid_a, s_a = a_request(pv)
    rid_b, s_b = a_request(pv)
    approve(pv, rid_b, pv.clock.t + 3 * HOUR)
    a = cl(pv.pv).get(f"/api/session-requests/status?s={s_a}").json()
    assert a["id"] == rid_a and a["link"] is None and a["ics_url"] is None
    # the status token is not an invite, and the request id is not a capability
    for tok in (s_a, s_b, rid_b):
        assert cl(pv.pv).get(f"/api/invites/ics?i={tok}").status_code == 404
        assert cl(pv.pv).get(f"/api/invites/status?i={tok}").json()["valid"] is False
    assert cl(pv.pv).get(f"/api/session-requests/status?s={rid_b}").status_code == 404


def test_ad_hoc_invites_have_no_calendar_and_ics_never_consumes(pv):  # noqa: F811
    tok = invite(pv)
    assert cl(pv.pv).get(f"/api/invites/ics?i={tok}").status_code == 404
    assert cl(pv.pv).get(f"/api/invites/status?i={tok}").json()["valid"] is True


def test_request_identity_never_reaches_visitors_testers_or_the_audit(pv):  # noqa: F811
    rid, _ = a_request(pv)
    tok = approve(pv, rid, pv.clock.t + 2 * HOUR).json()["link"].split("i=")[1]
    pv.clock.t += 2 * HOUR
    t, r = redeem(pv, tok)
    assert r.status_code == 200
    for c in (cl(pv.pv), t):
        for path in ("/api/audit", "/api/actions", "/api/lease", "/api/whoami"):
            blob = c.get(path).text
            assert NAME not in blob and CONTACT not in blob, path
    assert NAME not in json.dumps(pv.audit.query(limit=1000))


def test_markup_in_a_request_is_returned_as_json_text_only(pv):  # noqa: F811
    evil = "<img src=x onerror=alert(1)>"
    assert send(pv, name=evil[:80], message=evil).status_code == 201
    c, _ = op(pv)
    r = c.get("/api/admin/session-requests")
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["requests"][0]["message"] == evil


def test_oversized_and_flooded_requests_are_refused(pv):  # noqa: F811
    c = cl(pv.pv)
    assert c.post("/api/session-requests", json=body(pv, message="m" * 70000), headers=csrf(c)).status_code == 413
    b = body(pv)
    b["slots"] = b["slots"] * 200
    assert c.post("/api/session-requests", json=b, headers=csrf(c)).status_code == 422
    assert pv.lm.requests.pending_count() == 0


def test_requests_cannot_be_decided_by_get_or_by_the_requester(pv):  # noqa: F811
    rid, s = a_request(pv)
    c = cl(pv.pv)
    assert c.get(f"/api/admin/session-requests/{rid}/approve").status_code in (403, 404, 405)
    for verb in ("approve", "decline"):
        r = c.post(f"/api/admin/session-requests/{rid}/{verb}", json={"slot_start": body(pv)["slots"][0]},
                   headers={**csrf(c), "X-Status-Token": s})
        assert r.status_code == 403
    assert pv.lm.requests.get(rid)["status"] == "pending"


def test_an_early_link_cannot_be_redeemed_by_racing_the_clock_edge(pv):  # noqa: F811
    rid, _ = a_request(pv)
    start = pv.clock.t + 3 * HOUR
    tok = approve(pv, rid, start).json()["link"].split("i=")[1]
    pv.clock.t = start - 600 - 0.001
    assert redeem(pv, tok)[1].status_code == 425
    pv.clock.t = start - 600
    assert redeem(pv, tok)[1].status_code == 200
