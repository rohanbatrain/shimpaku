"""Tester passes: invites, leases, the queue, end-of-lease cleanup and retention (DEPLOY-DESIGN
build step 4).

**Invites.** The operator creates a single-use invite (``shimpaku admin invite create`` or
``POST /api/admin/invites``): a 256-bit token from ``secrets``, of which only the SHA-256 is
stored, an expiry (72 h by default) and a free-text note (operator-supplied; may hold a name). The
note is identity data: it never enters the audit chain, which records only the invite id.

**Redeem.** ``GET /start?i=<token>`` only shows the notice (a link preview cannot use the invite
up); ``POST /api/invites/redeem`` with consent consumes it atomically and creates a lease.

**Leases.** ``lease_max_active`` (1) at a time; the rest queue FIFO. States: ``queued`` →
``active`` → ``ending`` → ``ended`` (a queued lease may go straight to ``ending``). A lease ends
on timeout, the tester's "End session", or the operator's revoke. Ending is a persisted state and
the cleanup steps are recorded one by one, so a daemon restart resumes them:

1. cancel the lease's actions that have not started running;
2. revoke its MCP token(s) and delete its sessions;
3. delete its LiteLLM virtual key;
4. re-seed the fixture through ``service.demo_seed`` as role ``system`` (retried while the lab is
   busy, given up after ``RESEED_GIVE_UP_S``);

then the next queued lease is promoted. ``run_forever`` is the daemon task that drives this.

**Retention.** ``purge`` deletes the invite note and the display name ``retention_days`` (90)
after the lease ended (or after an unredeemed invite expired or was revoked). Audit rows are not
touched: they never held either.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import threading
import time
from typing import Any, Callable

from shimpaku.api.auth import AuthStore
from shimpaku.api.llm_keys import LiteLLMKeys, LLMKeyError

log = logging.getLogger("shimpaku.lease")

RESEED_GIVE_UP_S = 600
_CANCELLABLE = {"open", "awaiting_approval", "approved", "automatic"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS invite (
    invite_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, note TEXT, created_by TEXT,
    created_at REAL NOT NULL, expires_at REAL NOT NULL, redeemed_at REAL, revoked_at REAL,
    lease_id TEXT, purged_at REAL);
CREATE TABLE IF NOT EXISTS lease (
    lease_id TEXT PRIMARY KEY, invite_id TEXT, display_name TEXT, state TEXT NOT NULL,
    queued_at REAL NOT NULL, started_at REAL, ends_at REAL, end_requested_at REAL, ended_at REAL,
    end_reason TEXT, breaks INTEGER NOT NULL DEFAULT 0, seeds INTEGER NOT NULL DEFAULT 0,
    token_id TEXT, llm_key_minted INTEGER NOT NULL DEFAULT 0, cleanup TEXT NOT NULL DEFAULT '{}',
    purged_at REAL);
CREATE INDEX IF NOT EXISTS lease_state ON lease(state);
"""


class InviteError(Exception):
    """Redeem refused. ``code`` is one of invalid | used | expired | revoked | consent."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class LeaseError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _digest(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()


def tester_name(invite_id: str) -> str:
    """The principal name a tester acts under: the pass id, never the note or display name."""
    return f"tester:{invite_id}"


def _audit(caller: dict, method: str, args: dict, status: str = "ok") -> None:
    """One hash-chained row via the service layer (no-op outside the daemon)."""
    from shimpaku import service
    from shimpaku.actions.engine import Caller

    try:
        service._audit_row(Caller.coerce(caller), method, args=args, status=status)
    except Exception:  # noqa: BLE001  (auditing must not break a lease transition)
        log.exception("audit append failed for %s", method)


def _system(lease_id: str | None) -> dict:
    return {"kind": "system", "name": "lease-manager", "transport": "internal", "role": "system",
            "lease_id": lease_id}


class LeaseManager:
    def __init__(self, auth: AuthStore, service: Any, keys: LiteLLMKeys | None = None, *,
                 lease_s: float = 1800, max_active: int = 1, demo_ops: int = 5, invite_ttl_s: float = 72 * 3600,
                 retention_s: float = 90 * 86400, action_store: Callable[[], Any] | None = None,
                 clock: Callable[[], float] = time.time):
        self.auth = auth
        self.service = service
        self.keys = keys
        self.lease_s = lease_s
        self.max_active = max(1, int(max_active))
        self.demo_ops = demo_ops
        self.invite_ttl_s = invite_ttl_s
        self.retention_s = retention_s
        self.action_store = action_store
        self.clock = clock
        self._db = auth.db
        self._mu: threading.Lock = auth.lock
        self._keys_mem: dict[str, str] = {}  # lease_id -> LLM key value (memory only)
        self._tick_lock = asyncio.Lock()
        with self._mu:
            self._db.executescript(_SCHEMA)

    # ------------------------------------------------------------------ helpers
    def _q(self, sql: str, p: tuple = ()) -> list[dict]:
        with self._mu:
            cur = self._db.execute(sql, p)
            cols = [c[0] for c in cur.description or ()]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def _x(self, sql: str, p: tuple = ()) -> int:
        with self._mu:
            return self._db.execute(sql, p).rowcount

    def _lease_row(self, lease_id: str) -> dict | None:
        r = self._q("SELECT * FROM lease WHERE lease_id=?", (lease_id,))
        return r[0] if r else None

    def _set_cleanup(self, lease_id: str, step: str, value: Any) -> None:
        with self._mu:
            row = self._db.execute("SELECT cleanup FROM lease WHERE lease_id=?", (lease_id,)).fetchone()
            c = json.loads(row[0] or "{}") if row else {}
            c[step] = value
            self._db.execute("UPDATE lease SET cleanup=? WHERE lease_id=?", (json.dumps(c), lease_id))

    # ------------------------------------------------------------------ invites
    def create_invite(self, note: str, created_by: str, ttl_s: float | None = None) -> dict:
        token = secrets.token_urlsafe(32)  # 256 bits
        iid = "inv_" + secrets.token_hex(6)
        now = self.clock()
        exp = now + (ttl_s if ttl_s else self.invite_ttl_s)
        self._x("INSERT INTO invite (invite_id,token_hash,note,created_by,created_at,expires_at) VALUES (?,?,?,?,?,?)",
                (iid, _digest(token), (note or "")[:500], created_by, now, exp))
        _audit({"kind": "dashboard" if created_by != "cli" else "cli", "name": created_by, "role": "operator",
                "transport": "cli" if created_by == "cli" else "rest"},
               "invite_create", {"invite_id": iid, "expires": exp})
        return {"id": iid, "token": token, "expires": exp}

    def _invite_status_row(self, r: dict) -> str:
        if r["revoked_at"]:
            return "revoked"
        if r["redeemed_at"]:
            return "redeemed"
        if self.clock() >= r["expires_at"]:
            return "expired"
        return "open"

    def list_invites(self) -> list[dict]:
        rows = self._q("SELECT invite_id,note,created_by,created_at,expires_at,redeemed_at,revoked_at,lease_id,purged_at"
                       " FROM invite ORDER BY created_at DESC")
        return [{"id": r["invite_id"], "note": r["note"], "created_by": r["created_by"], "created": r["created_at"],
                 "expires": r["expires_at"], "redeemed": r["redeemed_at"], "revoked": r["revoked_at"],
                 "lease_id": r["lease_id"], "purged": r["purged_at"], "status": self._invite_status_row(r)}
                for r in rows]

    def revoke_invite(self, invite_id: str, by: str = "operator") -> bool:
        """Revoke an invite; if it was redeemed, its lease is ended (queued or active)."""
        rows = self._q("SELECT lease_id, revoked_at FROM invite WHERE invite_id=?", (invite_id,))
        if not rows or rows[0]["revoked_at"]:
            return False
        self._x("UPDATE invite SET revoked_at=? WHERE invite_id=?", (self.clock(), invite_id))
        _audit({"kind": "cli" if by == "cli" else "dashboard", "name": by, "role": "operator",
                "transport": "cli" if by == "cli" else "rest"}, "invite_revoke", {"invite_id": invite_id})
        if rows[0]["lease_id"]:
            self.request_end(rows[0]["lease_id"], "revoked")
        return True

    def invite_status(self, token: str) -> dict:
        """Does not consume. {valid, reason}."""
        rows = self._q("SELECT * FROM invite WHERE token_hash=?", (_digest(token or ""),))
        if not rows:
            return {"valid": False, "reason": "invalid"}
        st = self._invite_status_row(rows[0])
        return {"valid": st == "open", "reason": None if st == "open" else ("used" if st == "redeemed" else st)}

    def redeem(self, token: str, *, consent: bool, display_name: str | None = None) -> dict:
        """Consume the invite (atomically, once) and create a lease, queued or active."""
        if consent is not True:
            raise InviteError("consent", "consent is required to start the demo")
        now = self.clock()
        h = _digest(token or "")
        lease_id = "lse_" + secrets.token_hex(6)
        with self._mu:
            cur = self._db.execute(
                "UPDATE invite SET redeemed_at=?, lease_id=? WHERE token_hash=? AND redeemed_at IS NULL"
                " AND revoked_at IS NULL AND expires_at>?", (now, lease_id, h, now))
            if cur.rowcount != 1:
                row = self._db.execute("SELECT redeemed_at, revoked_at, expires_at FROM invite WHERE token_hash=?",
                                       (h,)).fetchone()
                if row is None:
                    raise InviteError("invalid", "this invite link is not valid")
                if row[1]:
                    raise InviteError("revoked", "this invite was revoked")
                if row[0]:
                    raise InviteError("used", "this invite has already been used")
                raise InviteError("expired", "this invite has expired")
            invite_id = self._db.execute("SELECT invite_id FROM invite WHERE token_hash=?", (h,)).fetchone()[0]
            name = (display_name or "").strip()[:60] or None
            self._db.execute("INSERT INTO lease (lease_id,invite_id,display_name,state,queued_at) VALUES (?,?,?,?,?)",
                             (lease_id, invite_id, name, "queued", now))
        _audit({"kind": "dashboard", "name": tester_name(invite_id), "role": "tester", "lease_id": lease_id,
                "transport": "rest"}, "invite_redeem", {"invite_id": invite_id, "lease_id": lease_id})
        self._promote_sync()
        return self.lease(lease_id)  # type: ignore[return-value]

    # ------------------------------------------------------------------ leases
    def _promote_sync(self) -> list[str]:
        started = []
        while True:
            now = self.clock()
            with self._mu:
                busy = self._db.execute("SELECT COUNT(*) FROM lease WHERE state IN ('active','ending')").fetchone()[0]
                if busy >= self.max_active:
                    break
                row = self._db.execute("SELECT lease_id FROM lease WHERE state='queued' ORDER BY queued_at, rowid"
                                       " LIMIT 1").fetchone()
                if row is None:
                    break
                self._db.execute("UPDATE lease SET state='active', started_at=?, ends_at=? WHERE lease_id=?",
                                 (now, now + self.lease_s, row[0]))
            started.append(row[0])
            _audit(_system(row[0]), "lease_start", {"lease_id": row[0], "ends": now + self.lease_s})
        return started

    def lease(self, lease_id: str | None) -> dict | None:
        r = self._lease_row(lease_id) if lease_id else None
        if r is None:
            return None
        now = self.clock()
        pos = None
        if r["state"] == "queued":
            pos = 1 + self._q("SELECT COUNT(*) AS n FROM lease WHERE state='queued' AND (queued_at<? OR"
                              " (queued_at=? AND rowid<(SELECT rowid FROM lease WHERE lease_id=?)))",
                              (r["queued_at"], r["queued_at"], lease_id))[0]["n"]
        return {"id": r["lease_id"], "invite_id": r["invite_id"], "state": r["state"],
                "queued": r["queued_at"], "started": r["started_at"], "ends": r["ends_at"],
                "ended": r["ended_at"], "end_reason": r["end_reason"],
                "remaining_s": max(0, int(r["ends_at"] - now)) if r["state"] == "active" and r["ends_at"] else None,
                "queue_position": pos, "lease_s": int(self.lease_s),
                "breaks_left": max(0, self.demo_ops - r["breaks"]), "seeds_left": max(0, self.demo_ops - r["seeds"]),
                "token_issued": bool(r["token_id"]), "display_name": r["display_name"]}

    def leases(self) -> list[dict]:
        ids = [r["lease_id"] for r in self._q("SELECT lease_id FROM lease ORDER BY queued_at DESC LIMIT 200")]
        return [self.lease(i) for i in ids]  # type: ignore[misc]

    def is_active(self, lease_id: str | None) -> bool:
        if not lease_id:
            return False
        r = self._lease_row(lease_id)
        return bool(r and r["state"] == "active" and self.clock() < (r["ends_at"] or 0))

    def is_live(self, lease_id: str | None) -> bool:
        """Queued or active: the tester's session is still valid."""
        r = self._lease_row(lease_id) if lease_id else None
        return bool(r and (r["state"] == "queued" or (r["state"] == "active" and self.clock() < (r["ends_at"] or 0))))

    def request_end(self, lease_id: str, reason: str) -> bool:
        """Mark a queued or active lease as ending; the cleanup runs in ``tick`` (or ``end``)."""
        n = self._x("UPDATE lease SET state='ending', end_reason=?, end_requested_at=? WHERE lease_id=?"
                    " AND state IN ('queued','active')", (reason[:40], self.clock(), lease_id))
        if n:
            # the session and token stop working at once, before the slower steps
            self.auth.revoke_lease_tokens(lease_id)
            self.auth.drop_lease_sessions(lease_id)
            _audit(_system(lease_id), "lease_end", {"lease_id": lease_id, "reason": reason[:40]})
        return bool(n)

    async def end(self, lease_id: str, reason: str) -> dict | None:
        self.request_end(lease_id, reason)
        await self.tick()
        return self.lease(lease_id)

    def use_demo_op(self, lease_id: str, op: str) -> bool:
        col = "breaks" if op == "break" else "seeds"
        return self._x(f"UPDATE lease SET {col}={col}+1 WHERE lease_id=? AND state='active' AND {col}<?",
                       (lease_id, self.demo_ops)) == 1

    # ------------------------------------------------------------------ token + LLM key
    def issue_token(self, lease_id: str) -> dict:
        """The lease's one MCP token: scope propose, role tester, expires with the lease."""
        r = self._lease_row(lease_id)
        if r is None or not self.is_active(lease_id):
            raise LeaseError("inactive", "the lease is not active")
        name = f"{tester_name(r['invite_id'])}-agent"
        with self._mu:
            claimed = self._db.execute("UPDATE lease SET token_id='pending' WHERE lease_id=? AND token_id IS NULL",
                                       (lease_id,)).rowcount
        if not claimed:
            raise LeaseError("exists", "this lease already has its MCP token; it is shown only once")
        tok = self.auth.create_token(name, "propose", tester_name(r["invite_id"]), expires_at=r["ends_at"],
                                     role="tester", lease_id=lease_id)
        self._x("UPDATE lease SET token_id=? WHERE lease_id=?", (tok["id"], lease_id))
        _audit({"kind": "dashboard", "name": tester_name(r["invite_id"]), "role": "tester", "lease_id": lease_id,
                "transport": "rest"}, "lease_token", {"lease_id": lease_id, "token_id": tok["id"]})
        return tok

    def verify_token(self, value: str) -> dict | None:
        """MCP/REST token verifier: live token, and a tester token only while its lease is active."""
        rec = self.auth.token_record(value)
        if rec is None:
            return None
        if rec.get("role") == "tester" and not self.is_active(rec.get("lease_id")):
            return None
        return rec

    async def llm_key(self, lease_id: str) -> str | None:
        """The lease's LiteLLM key, minted on first use. None when no admin key is configured."""
        if self.keys is None or not self.keys.enabled or not self.is_active(lease_id):
            return None
        if lease_id in self._keys_mem:
            return self._keys_mem[lease_id]
        r = self._lease_row(lease_id)
        if r and r["llm_key_minted"]:  # minted before a restart: the value is gone, replace it
            try:
                await self.keys.delete(lease_id)
            except LLMKeyError:
                pass
        remaining = max(60.0, (r["ends_at"] or 0) - self.clock()) if r else self.lease_s
        key = await self.keys.generate(lease_id, remaining)
        self._x("UPDATE lease SET llm_key_minted=1 WHERE lease_id=?", (lease_id,))
        self._keys_mem[lease_id] = key
        return key

    # ------------------------------------------------------------------ cleanup
    async def _cleanup(self, r: dict) -> bool:
        lid = r["lease_id"]
        c = json.loads(r["cleanup"] or "{}")
        if "actions" not in c:
            self._set_cleanup(lid, "actions", self._cancel_actions(lid))
        if "token" not in c:
            self.auth.revoke_lease_tokens(lid)
            self.auth.drop_lease_sessions(lid)
            self._set_cleanup(lid, "token", True)
        if "llm_key" not in c:
            key = self._keys_mem.pop(lid, None)
            if r["llm_key_minted"] and self.keys is not None:
                try:
                    await self.keys.delete(lid, key)
                except LLMKeyError as e:
                    if key:
                        self._keys_mem[lid] = key
                    log.warning("lease %s: LLM key delete failed (%s); retrying", lid, e)
                    return False
            self._set_cleanup(lid, "llm_key", True)
        if "reseed" not in c:
            if r["started_at"] is None:
                self._set_cleanup(lid, "reseed", "not needed")
            else:
                out = await _maybe_await(self.service.demo_seed(_system(lid)))
                err = out.get("error") if isinstance(out, dict) else None
                if err:
                    if self.clock() - (r["end_requested_at"] or 0) < RESEED_GIVE_UP_S:
                        log.warning("lease %s: re-seed failed (%s); retrying", lid, err.get("code"))
                        return False
                    self._set_cleanup(lid, "reseed", f"gave up: {err.get('code')}")
                else:
                    self._set_cleanup(lid, "reseed", True)
        self._x("UPDATE lease SET state='ended', ended_at=? WHERE lease_id=?", (self.clock(), lid))
        return True

    def _cancel_actions(self, lease_id: str) -> int:
        if self.action_store is None:
            return 0
        from shimpaku import service
        from shimpaku.actions.engine import Caller, Engine
        from shimpaku.actions.state import StateError

        store = self.action_store()
        n = 0
        for a in store.list(limit=1000):
            if a["status"] in _CANCELLABLE and (a.get("caller") or {}).get("lease_id") == lease_id:
                try:
                    out = store.transition(a["id"], "cancelled", "lease ended", expect=_CANCELLABLE)
                except StateError:
                    continue
                n += 1
                service._audit_row(Caller.coerce(_system(lease_id)), "action_cancel",
                                   args={"action_id": a["id"]}, action=Engine.view(out), status="cancelled")
        return n

    async def tick(self) -> None:
        """Expire timed-out leases, finish ending ones, promote the queue."""
        async with self._tick_lock:
            now = self.clock()
            for r in self._q("SELECT lease_id FROM lease WHERE state='active' AND ends_at<=?", (now,)):
                self.request_end(r["lease_id"], "timeout")
            for r in self._q("SELECT * FROM lease WHERE state='ending' ORDER BY end_requested_at"):
                try:
                    await self._cleanup(r)
                except Exception:  # noqa: BLE001
                    log.exception("lease %s cleanup failed; retrying", r["lease_id"])
            self._promote_sync()

    # ------------------------------------------------------------------ retention
    def purge(self, now: float | None = None) -> dict:
        """Delete tester identity data (invite note, display name) older than the retention."""
        cutoff = (now if now is not None else self.clock()) - self.retention_s
        stamp = self.clock()
        with self._mu:
            leases = self._db.execute(
                "UPDATE lease SET display_name=NULL, purged_at=? WHERE state='ended' AND ended_at<? AND purged_at IS NULL",
                (stamp, cutoff)).rowcount
            invites = self._db.execute(
                "UPDATE invite SET note=NULL, purged_at=? WHERE purged_at IS NULL AND ("
                " lease_id IN (SELECT lease_id FROM lease WHERE state='ended' AND ended_at<?)"
                " OR (redeemed_at IS NULL AND (expires_at<? OR revoked_at<?)))",
                (stamp, cutoff, cutoff, cutoff)).rowcount
        if leases or invites:
            _audit(_system(None), "tester_purge", {"leases": leases, "invites": invites})
        return {"leases": leases, "invites": invites}

    async def run_forever(self, interval: float = 5.0, purge_every: float = 86400.0) -> None:
        """The daemon task: lease timers every ``interval`` s, purge once a day (and at start)."""
        last_purge = 0.0
        while True:
            try:
                await self.tick()
                if time.monotonic() - last_purge >= purge_every or last_purge == 0.0:
                    await asyncio.to_thread(self.purge)
                    last_purge = time.monotonic()
            except Exception:  # noqa: BLE001
                log.exception("lease tick failed")
            await asyncio.sleep(interval)


async def _maybe_await(v: Any) -> Any:
    if asyncio.iscoroutine(v) or isinstance(v, asyncio.Future):
        return await v
    return v


def from_settings(auth: AuthStore, service: Any, *, action_store: Callable[[], Any] | None = None) -> LeaseManager:
    from shimpaku.config import settings

    keys = LiteLLMKeys(settings.llm_base, settings.llm_admin_key, budget_usd=settings.tester_llm_budget_usd,
                       rpm=settings.tester_llm_rpm, models=settings.tester_models())
    return LeaseManager(auth, service, keys, lease_s=settings.lease_minutes * 60, max_active=settings.lease_max_active,
                        demo_ops=settings.lease_demo_ops, invite_ttl_s=settings.invite_ttl_hours * 3600,
                        retention_s=settings.retention_days * 86400, action_store=action_store)
