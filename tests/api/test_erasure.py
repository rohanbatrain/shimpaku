"""Self-service erasure: a requester with the status token, a tester from the session or with the
invite link. No mailbox needed. Fakes only: no lab, no gateway."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "actions"))
from test_engine import box  # noqa: E402,F401
from test_public_view import CONTACT, HOUR, MSG, NAME, a_request, app_for, approve, cl, op, pv, redeem  # noqa: E402,F401
from test_tester_passes import NOTE, csrf, invite, world  # noqa: E402,F401
from test_tester_passes import redeem as redeem_named  # noqa: E402

from shimpaku.actions import fixture  # noqa: E402

_ = (pv, box, world)
TESTER = "Tester Display Name"


def erase(w, s, c=None, path="/api/session-requests/erase"):
    c = c or cl(w.pv)
    return c.post(path, json={"token": s}, headers=csrf(c))


def raw(w, rid):
    return w.lm._q("SELECT * FROM session_request WHERE request_id=?", (rid,))[0]


def test_erase_pending_request_wipes_everything_but_id_status_and_times(pv):  # noqa: F811
    rid, s = a_request(pv)
    r = erase(pv, s)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["id"] == rid and out["status"] == "erased" and out["slots"] == [] and out["link"] is None
    row = raw(pv, rid)
    for k in ("name", "contact", "message", "decline_note", "invite_token", "slot_start", "slot_end"):
        assert row[k] is None, k
    assert row["slots"] == "[]" and row["created_at"] and row["closed_at"]
    blob = json.dumps(row)
    assert NAME not in blob and CONTACT not in blob and MSG not in blob
    # the operator sees it erased, and the status page says so
    c, _ = op(pv)
    assert c.get(f"/api/admin/session-requests/{rid}").json()["name"] is None
    assert cl(pv.pv).get(f"/api/session-requests/status?s={s}").json()["status"] == "erased"
    assert pv.lm.requests.pending_count() == 0


def test_erased_request_cannot_be_approved_or_declined(pv):  # noqa: F811
    rid, s = a_request(pv)
    assert erase(pv, s).status_code == 200
    assert approve(pv, rid, pv.clock.t + 3 * HOUR).status_code == 409
    c, h = op(pv)
    assert c.post(f"/api/admin/session-requests/{rid}/decline", json={"note": "x"}, headers=h).status_code == 409
    assert raw(pv, rid)["status"] == "erased" and raw(pv, rid)["invite_id"] is None


def test_erase_is_idempotent_and_audited_with_the_id_only(pv):  # noqa: F811
    rid, s = a_request(pv)
    assert erase(pv, s).status_code == 200
    assert erase(pv, s).status_code == 200
    rows = [r for r in pv.audit.query(limit=1000) if r["tool"] == "session_request_erase"]
    assert len(rows) == 1 and rows[0]["args"] == {"request_id": rid}
    blob = json.dumps(pv.audit.query(limit=1000))
    assert NAME not in blob and CONTACT not in blob and MSG not in blob


def test_erase_needs_csrf_and_same_origin(pv):  # noqa: F811
    rid, s = a_request(pv)
    c = cl(pv.pv)
    assert c.post("/api/session-requests/erase", json={"token": s}).status_code == 403
    h = csrf(c)
    assert c.post("/api/session-requests/erase", json={"token": s},
                  headers={**h, "Origin": "https://evil.example"}).status_code == 403
    assert raw(pv, rid)["name"] == NAME


def test_wrong_and_malformed_tokens_are_the_same_404(pv):  # noqa: F811
    rid, s = a_request(pv)
    replies = [erase(pv, t) for t in ("", "x", s[:-1] + ("A" if s[-1] != "A" else "B"), rid, "z" * 5000)]
    assert {r.status_code for r in replies} == {404}
    assert len({r.text for r in replies}) == 1
    assert raw(pv, rid)["name"] == NAME


def test_erase_is_rate_limited_per_address(pv):  # noqa: F811
    c = cl(app_for(pv, table={"erase": (2, 100)}))
    assert [erase(pv, "nope", c).status_code for _ in range(3)] == [404, 404, 429]


def test_erase_approved_request_revokes_its_invite(pv):  # noqa: F811
    rid, s = a_request(pv)
    tok = approve(pv, rid, pv.clock.t + 2 * HOUR).json()["link"].split("i=")[1]
    assert erase(pv, s).status_code == 200
    pv.clock.t += 2 * HOUR
    _, r = redeem(pv, tok)
    assert r.status_code == 410
    inv = pv.lm._q("SELECT * FROM invite WHERE invite_id=?", (raw(pv, rid)["invite_id"],))[0]
    assert inv["revoked_at"] and inv["note"] is None


def test_erase_during_the_session_ends_the_lease_and_cleans_up(pv):  # noqa: F811
    rid, s = a_request(pv)
    tok = approve(pv, rid, pv.clock.t + 2 * HOUR).json()["link"].split("i=")[1]
    pv.clock.t += 2 * HOUR
    t, r = redeem(pv, tok)
    lease_id = r.json()["lease"]["id"]
    a = t.post("/api/actions", json={"kind": fixture.KIND, "args": fixture.ARGS, "reason": "fix"}, headers=csrf(t)).json()
    seeds = len(pv.seeder.calls)
    assert erase(pv, s).status_code == 200
    assert pv.lm.lease(lease_id)["state"] == "ended" and pv.lm.lease(lease_id)["end_reason"] == "erased"
    assert pv.eng.store.get(a["id"])["status"] == "cancelled"
    assert len(pv.seeder.calls) == seeds + 1
    assert t.get("/api/whoami").json()["role"] == "visitor"


# ---- testers -------------------------------------------------------------------------------------

def _invite_row(w, iid):
    return w.lm._q("SELECT * FROM invite WHERE invite_id=?", (iid,))[0]


def test_tester_erases_from_the_session(world):  # noqa: F811
    t, r = redeem_named(world, invite(world), name=TESTER)
    lease = r.json()["lease"]
    a = t.post("/api/actions", json={"kind": fixture.KIND, "args": fixture.ARGS, "reason": "fix"}, headers=csrf(t)).json()
    assert t.post("/api/lease/erase").status_code == 403  # CSRF
    r = t.post("/api/lease/erase", headers=csrf(t))
    assert r.status_code == 200, r.text
    assert world.lm.lease(lease["id"])["state"] == "ended" and world.lm.lease(lease["id"])["display_name"] is None
    assert _invite_row(world, lease["invite_id"])["note"] is None
    assert world.eng.store.get(a["id"])["status"] == "cancelled"
    assert t.post("/api/demo/break", headers=csrf(t)).status_code in (401, 403)
    blob = json.dumps(world.audit.query(limit=1000))
    assert NOTE not in blob and TESTER not in blob
    assert any(x["tool"] == "tester_erase" and x["args"] == {"invite_id": lease["invite_id"]}
               for x in world.audit.query(limit=1000))


def test_operator_and_visitor_cannot_use_the_tester_erase(world):  # noqa: F811
    c, h = __import__("test_tester_passes").operator(world)
    assert c.post("/api/lease/erase", headers=h).status_code == 403


def test_tester_erases_with_the_invite_link_after_the_lease_ended(world):  # noqa: F811
    tok = invite(world)
    t, r = redeem_named(world, tok, name=TESTER)
    lease = r.json()["lease"]
    assert t.post("/api/lease/end", headers=csrf(t)).status_code == 200
    c = cl(world.app)
    assert c.post("/api/invites/erase", json={"token": tok}).status_code == 403  # CSRF
    r = c.post("/api/invites/erase", json={"token": tok}, headers=csrf(c))
    assert r.status_code == 200, r.text
    assert world.lm.lease(lease["id"])["display_name"] is None and _invite_row(world, lease["invite_id"])["note"] is None
    # idempotent; unknown tokens are one 404
    assert c.post("/api/invites/erase", json={"token": tok}, headers=csrf(c)).status_code == 200
    bad = [c.post("/api/invites/erase", json={"token": x}, headers=csrf(c)) for x in ("", "nope", "y" * 5000)]
    assert {b.status_code for b in bad} == {404} and len({b.text for b in bad}) == 1


def test_invite_link_erase_works_on_expired_and_unused_invites(world):  # noqa: F811
    tok = invite(world)
    world.clock.t += 73 * 3600
    c = cl(world.app)
    assert c.post("/api/invites/erase", json={"token": tok}, headers=csrf(c)).status_code == 200
    tok2 = invite(world)
    assert c.post("/api/invites/erase", json={"token": tok2}, headers=csrf(c)).status_code == 200
    _, r = redeem_named(world, tok2)
    assert r.status_code == 410  # an erased open invite is withdrawn too
    assert all(r["note"] is None for r in world.lm._q("SELECT note FROM invite"))


def test_invite_link_erase_also_erases_the_request_behind_it(pv):  # noqa: F811
    rid, s = a_request(pv)
    tok = approve(pv, rid, pv.clock.t + 2 * HOUR).json()["link"].split("i=")[1]
    c = cl(pv.pv)
    assert c.post("/api/invites/erase", json={"token": tok}, headers=csrf(c)).status_code == 200
    assert raw(pv, rid)["status"] == "erased" and raw(pv, rid)["name"] is None
