"""Session requests: a stranger asks for a live session; the operator approves a slot.

**Request.** ``POST /api/session-requests`` (public, CSRF, consent, rate-limited per address)
stores a name, a contact (an e-mail address or a LinkedIn URL), a message (<= 500 chars) and 1-3
preferred slot starts. The requester gets back a **status token** (256 bits; only its SHA-256 is
stored): ``GET /api/session-requests/status?s=`` is the capability to see the decision, and the
invite link once approved. A wrong token is a 404, the same as a malformed one.

**Approve.** The operator picks a slot start (one of the requested slots or any other) and
gets a single-use invite bound to it: redeemable from ``start - 10 min`` until ``end``
(``end = start + lease length``), and its lease ends at ``min(start of the lease + lease length,
end + 5 min)``. The invite token is kept in this row (``invite_token``) so the requester's status
page can show the link; it is wiped when the invite is used, the slot ends, or the request is
declined. The invite's note is the request id, never the requester's name.

**States.** ``pending`` -> ``approved`` | ``declined`` | ``expired`` (every requested slot passed);
``approved`` -> ``used`` (invite redeemed) | ``expired`` (slot ended unused) | ``declined``
(operator withdrew it; the invite is revoked). ``sweep`` applies the time-based moves.

**Retention.** ``purge`` deletes name, contact, message and decline note ``retention_days`` after
the slot ended (approved/used/expired after approval), after the decline, or after the last
requested slot (expired while pending). Audit rows carry only the request id.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from shimpaku.api.lease import LeaseManager

OPEN_BEFORE_S = 600       # the invite opens 10 min before the slot
LEASE_GRACE_S = 300       # the lease may run 5 min past the slot end
MIN_LEAD_S = 3600         # requested slots start >= 1 h from now
MAX_AHEAD_S = 30 * 86400  # ... and <= 30 days from now
MAX_SLOTS = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS session_request (
    request_id TEXT PRIMARY KEY, status_hash TEXT NOT NULL UNIQUE, status TEXT NOT NULL,
    name TEXT, contact TEXT, message TEXT, slots TEXT NOT NULL, created_at REAL NOT NULL,
    decided_at REAL, decided_by TEXT, decline_note TEXT, slot_start REAL, slot_end REAL,
    invite_id TEXT, invite_token TEXT, closed_at REAL, purged_at REAL);
CREATE INDEX IF NOT EXISTS session_request_status ON session_request(status);
"""

EMAIL = re.compile(r"^[^@\s<>]{1,64}@[^@\s<>]+\.[^@\s<>]{2,}$")
LINKEDIN = re.compile(r"^(https?://)?([a-z]{2,3}\.)?linkedin\.com/[^\s<>]{2,}$", re.I)


class RequestError(Exception):
    """``code``: bad_argument | consent | full | not_found | bad_state."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _digest(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()


def valid_contact(contact: str) -> bool:
    c = contact.strip()
    return bool(EMAIL.match(c) or LINKEDIN.match(c))


class SessionRequests:
    def __init__(self, lm: "LeaseManager"):
        self.lm = lm
        self._db = lm._db
        self._mu = lm._mu
        with self._mu:
            self._db.executescript(_SCHEMA)

    @property
    def clock(self):
        return self.lm.clock

    @property
    def slot_s(self) -> float:
        return self.lm.lease_s

    def _q(self, sql: str, p: tuple = ()) -> list[dict]:
        return self.lm._q(sql, p)

    def _x(self, sql: str, p: tuple = ()) -> int:
        return self.lm._x(sql, p)

    # ------------------------------------------------------------------ create
    def create(self, *, name: str, contact: str, message: str, slots: list[float], consent: bool,
               max_pending: int = 200) -> dict:
        """Store a request. ``slots`` are epoch seconds. Returns {id, status_token}."""
        from shimpaku.api.lease import _audit

        if consent is not True:
            raise RequestError("consent", "consent is required to send a request")
        name, contact, message = (name or "").strip(), (contact or "").strip(), (message or "").strip()
        if not 1 <= len(name) <= 80:
            raise RequestError("bad_argument", "give a name of 1-80 characters")
        if len(contact) > 200 or not valid_contact(contact):
            raise RequestError("bad_argument", "contact must be an e-mail address or a LinkedIn URL")
        if len(message) > 500:
            raise RequestError("bad_argument", "the message is limited to 500 characters")
        if not 1 <= len(slots) <= MAX_SLOTS:
            raise RequestError("bad_argument", f"give 1-{MAX_SLOTS} preferred slots")
        now = self.clock()
        for t in slots:
            if t < now + MIN_LEAD_S or t > now + MAX_AHEAD_S:
                raise RequestError("bad_argument", "each slot must start at least 1 hour and at most 30 days from now")
        self.sweep()
        if self._q("SELECT COUNT(*) AS n FROM session_request WHERE status='pending'")[0]["n"] >= max_pending:
            raise RequestError("full", "too many open requests right now; try again later")
        token = secrets.token_urlsafe(32)
        rid = "req_" + secrets.token_hex(6)
        self._x("INSERT INTO session_request (request_id,status_hash,status,name,contact,message,slots,created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (rid, _digest(token), "pending", name, contact, message, json.dumps(sorted(set(slots))), now))
        _audit({"kind": "dashboard", "name": "visitor", "role": "visitor", "transport": "rest"},
               "session_request_create", {"request_id": rid})
        return {"id": rid, "status_token": token}

    # ------------------------------------------------------------------ time
    def sweep(self) -> dict:
        """pending with every slot past -> expired; approved -> used (redeemed) or expired (slot ended)."""
        now = self.clock()
        exp_p = 0
        for r in self._q("SELECT request_id, slots FROM session_request WHERE status='pending'"):
            if max(json.loads(r["slots"])) + self.slot_s <= now:
                exp_p += self._x("UPDATE session_request SET status='expired', closed_at=? WHERE request_id=?"
                                 " AND status='pending'", (max(json.loads(r["slots"])) + self.slot_s, r["request_id"]))
        used = self._x("UPDATE session_request SET status='used', invite_token=NULL WHERE status='approved' AND invite_id"
                       " IN (SELECT invite_id FROM invite WHERE redeemed_at IS NOT NULL)")
        exp_a = self._x("UPDATE session_request SET status='expired', invite_token=NULL, closed_at=slot_end"
                        " WHERE status='approved' AND slot_end<=?", (now,))
        return {"expired": exp_p + exp_a, "used": used}

    # ------------------------------------------------------------------ views
    def _row(self, rid: str) -> dict | None:
        r = self._q("SELECT * FROM session_request WHERE request_id=?", (rid,))
        return r[0] if r else None

    def _view(self, r: dict, *, operator: bool) -> dict:
        out = {"id": r["request_id"], "status": r["status"], "slots": json.loads(r["slots"]),
               "created": r["created_at"], "decided": r["decided_at"], "slot_start": r["slot_start"],
               "slot_end": r["slot_end"], "opens_at": (r["slot_start"] - OPEN_BEFORE_S) if r["slot_start"] else None,
               "decline_note": r["decline_note"]}
        if operator:
            out.update(name=r["name"], contact=r["contact"], message=r["message"], decided_by=r["decided_by"],
                       invite_id=r["invite_id"], purged=r["purged_at"])
        return out

    def list(self, status: str | None = None) -> list[dict]:
        self.sweep()
        rows = self._q("SELECT * FROM session_request" + (" WHERE status=?" if status else "")
                       + " ORDER BY created_at DESC, rowid DESC LIMIT 500", (status,) if status else ())
        return [self._view(r, operator=True) for r in rows]

    def get(self, rid: str) -> dict:
        self.sweep()
        r = self._row(rid)
        if r is None:
            raise RequestError("not_found", "no such request")
        return self._view(r, operator=True)

    def pending_count(self) -> int:
        self.sweep()
        return self._q("SELECT COUNT(*) AS n FROM session_request WHERE status='pending'")[0]["n"]

    def status(self, token: str) -> dict | None:
        """The requester's view by status token; None for any unknown token. Includes the invite
        token while the request is approved and the slot has not ended."""
        if not token or len(token) > 200:
            return None
        self.sweep()
        rows = self._q("SELECT * FROM session_request WHERE status_hash=?", (_digest(token),))
        if not rows:
            return None
        r = rows[0]
        return {**self._view(r, operator=False),
                "invite_token": r["invite_token"] if r["status"] == "approved" else None}

    # ------------------------------------------------------------------ decide
    def approve(self, rid: str, slot_start: float, by: str) -> dict:
        """Bind an invite to [slot_start, slot_start + lease length]. Returns the view + invite token."""
        from shimpaku.api.lease import _audit

        self.sweep()
        r = self._row(rid)
        if r is None:
            raise RequestError("not_found", "no such request")
        if r["status"] != "pending":
            raise RequestError("bad_state", f"the request is {r['status']}; only a pending request is approved")
        now = self.clock()
        end = slot_start + self.slot_s
        if end <= now + 60 or slot_start > now + 2 * MAX_AHEAD_S:
            raise RequestError("bad_argument", "the slot must end in the future and start within 60 days")
        inv = self.lm.create_invite(f"session request {rid}", by, not_before=slot_start - OPEN_BEFORE_S,
                                    expires_at=end, lease_cap=end + LEASE_GRACE_S)
        n = self._x("UPDATE session_request SET status='approved', decided_at=?, decided_by=?, slot_start=?, slot_end=?,"
                    " invite_id=?, invite_token=? WHERE request_id=? AND status='pending'",
                    (now, by, slot_start, end, inv["id"], inv["token"], rid))
        if n != 1:  # lost a race with another decision: withdraw the invite just made
            self.lm.revoke_invite(inv["id"], by)
            raise RequestError("bad_state", "the request was decided meanwhile")
        _audit({"kind": "cli" if by == "cli" else "dashboard", "name": by, "role": "operator",
                "transport": "cli" if by == "cli" else "rest"}, "session_request_approve",
               {"request_id": rid, "invite_id": inv["id"], "slot_start": slot_start})
        return {**self.get(rid), "invite_token": inv["token"]}

    def decline(self, rid: str, note: str, by: str) -> dict:
        from shimpaku.api.lease import _audit

        self.sweep()
        r = self._row(rid)
        if r is None:
            raise RequestError("not_found", "no such request")
        if r["status"] not in ("pending", "approved"):
            raise RequestError("bad_state", f"the request is {r['status']}")
        now = self.clock()
        n = self._x("UPDATE session_request SET status='declined', decided_at=?, decided_by=?, decline_note=?,"
                    " invite_token=NULL, closed_at=? WHERE request_id=? AND status=?",
                    (now, by, (note or "").strip()[:500] or None, now, rid, r["status"]))
        if n != 1:
            raise RequestError("bad_state", "the request was decided meanwhile")
        if r["invite_id"]:
            self.lm.revoke_invite(r["invite_id"], by)
        _audit({"kind": "cli" if by == "cli" else "dashboard", "name": by, "role": "operator",
                "transport": "cli" if by == "cli" else "rest"}, "session_request_decline", {"request_id": rid})
        return self.get(rid)

    # ------------------------------------------------------------------ calendar
    def ics_for_request(self, rid: str, link: str | None) -> str:
        r = self._row(rid)
        if r is None or not r["slot_start"]:
            raise RequestError("not_found", "no approved slot for this request")
        return ics(rid, r["slot_start"], r["slot_end"], link, self.clock())

    def request_for_invite(self, invite_token: str) -> dict | None:
        """The approved request whose invite has this token (does not consume the invite)."""
        rows = self._q("SELECT r.* FROM session_request r JOIN invite i ON i.invite_id=r.invite_id"
                       " WHERE i.token_hash=?", (_digest(invite_token or ""),))
        return rows[0] if rows else None

    # ------------------------------------------------------------------ retention
    def purge(self, cutoff: float, stamp: float) -> int:
        """Delete identity data of requests closed before ``cutoff``."""
        self.sweep()
        with self._mu:
            # closed_at: slot end (used/expired after approval), decline time, or last slot (expired pending)
            self._db.execute("UPDATE session_request SET closed_at=slot_end WHERE status='used' AND closed_at IS NULL")
            return self._db.execute(
                "UPDATE session_request SET name=NULL, contact=NULL, message=NULL, decline_note=NULL,"
                " invite_token=NULL, purged_at=? WHERE purged_at IS NULL AND closed_at IS NOT NULL AND closed_at<?",
                (stamp, cutoff)).rowcount


# ---------------------------------------------------------------------- RFC 5545
def _ics_time(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _ics_text(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Fold to 75 octets per line (RFC 5545 3.1), never splitting a UTF-8 sequence."""
    out, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode())
        if size + n > (75 if not out else 74):
            out.append(cur)
            cur, size = "", 0
        cur += ch
        size += n
    out.append(cur)
    return "\r\n ".join(out)


def ics(rid: str, start: float, end: float, link: str | None, now: float) -> str:
    desc = ("Your Shimpaku live session (30 minutes, one switch port in a real lab). "
            + (f"Join: {link} (the link opens 10 minutes before the start and works once)." if link else
               "Use the link from your request's status page; it opens 10 minutes before the start."))
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Shimpaku//Live session//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "BEGIN:VEVENT", f"UID:{rid}@shimpaku", f"DTSTAMP:{_ics_time(now)}",
             f"DTSTART:{_ics_time(start)}", f"DTEND:{_ics_time(end)}", "SUMMARY:Shimpaku live session",
             f"DESCRIPTION:{_ics_text(desc)}"]
    if link:
        lines.append(f"URL:{link}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "".join(_fold(x) + "\r\n" for x in lines)


def as_epoch(dt: Any) -> float:
    return dt.timestamp() if isinstance(dt, datetime) else float(dt)
