"""Auth for the dashboard and agents (R8 §5, CONTRACT "auth flag").

Modes: ``off`` (no sign-in; the browser user is ``operator``) and ``local`` (one ``admin`` whose
argon2id hash comes from ``SHIMPAKU_ADMIN_PASSWORD_HASH`` or ``<data>/admin.hash``).

Principals:
- a **session user** (browser cookie) may read, propose, cancel and approve; an operator session
  may act on any action, a tester session only on the demo fixture of its own lease (enforced in
  ``shimpaku.actions.engine``, below this layer, so MCP gets the same rule);
- a **bearer token** (agents) has scope ``read`` or ``propose`` and can never approve.

Sessions and tokens live in SQLite (``state.db`` tables ``session`` and ``token``). Only SHA-256
digests of session ids and tokens are stored, never the values.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

SESSION_IDLE_S = 12 * 3600
SESSION_ABSOLUTE_S = 7 * 24 * 3600
LOGIN_LIMIT_PER_MIN = 5
SCOPES = ("read", "propose")
ADMIN_USER = "admin"
OFF_MODE_USER = "operator"

# Same DDL as shimpaku.store (state.db, owner B-telemetry); repeated here so the API also works on
# an in-memory DB in tests. Times are ISO-8601 UTC text. `session_id` holds the SHA-256 of the
# cookie value, never the value. Idle expiry slides `expires_at` forward, capped at created + 7 d.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS token (
    token_id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE, scopes TEXT NOT NULL,
    created_by TEXT, created_at TEXT NOT NULL, last_used TEXT, revoked_at TEXT);
CREATE TABLE IF NOT EXISTS session (
    session_id TEXT PRIMARY KEY, user TEXT NOT NULL, csrf TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
"""

_ph = PasswordHasher()  # argon2id with argon2-cffi's RFC 9106 low-memory defaults


def hash_password(password: str) -> str:
    return _ph.hash(password)


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")


def _ts(iso: str | None) -> float | None:
    return datetime.fromisoformat(iso).timestamp() if iso else None


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class Principal:
    kind: str  # "session" | "token" | "anonymous"
    name: str  # "admin", "operator", "token:<name>"
    scope: str  # "admin" for session users, else the token scope
    token_id: str | None = None
    # operator | tester | visitor | agent. None = derived from kind: a session is the operator, a
    # token is the operator's agent, anonymous is a visitor. Tester principals (sessions and
    # tokens bound to one lease) are issued by the lease manager (DEPLOY-DESIGN build step 4).
    role: str | None = None
    lease_id: str | None = None
    # Presenter mode: an operator session showing the visitor-style UI (shimpaku.api.routes_console).
    # Only ever True on an operator session; authority stays the operator's.
    presenting: bool = False

    @property
    def effective_role(self) -> str:
        if self.role is not None:
            return self.role
        return {"session": "operator", "token": "agent"}.get(self.kind, "visitor")

    def caller(self, transport: str = "rest") -> dict:
        """The service-layer caller (shimpaku.actions.engine.Caller fields)."""
        kind = "dashboard" if self.kind == "session" else "mcp"
        return {"kind": kind, "name": self.name, "token_id": self.token_id, "transport": transport,
                "role": self.effective_role, "lease_id": self.lease_id}

    @property
    def can_approve(self) -> bool:
        return self.kind == "session"

    @property
    def is_operator(self) -> bool:
        """Operator-only surfaces (invites, leases, tokens): an operator's dashboard session."""
        return self.kind == "session" and self.effective_role == "operator"

    @property
    def can_present(self) -> bool:
        """Presenter features (the Junos console): the operator's own dashboard session only.
        Never a token, a tester or a visitor, whatever the ``presenting`` flag says."""
        return self.is_operator and self.scope == "admin" and self.token_id is None

    @property
    def can_propose(self) -> bool:
        return self.kind == "session" or self.scope == "propose"


class AuthStore:
    """Sessions, tokens and the login rate limiter. Thread-safe; one SQLite connection."""

    def __init__(self, path: Path | str | None):
        target = ":memory:" if path is None else str(path)
        if path is not None:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(target, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL") if path is not None else None
        self._db.executescript(_SCHEMA)
        self._migrate()
        self._lock = threading.Lock()
        self._logins: dict[str, deque[float]] = {}
        self.clock = time.time  # token expiry clock (tests replace it)
        self.session_clock = time.time  # session expiry clock (tests replace it)

    # Columns added after the first release. Existing token rows get NULL expires_at = no expiry
    # (the operator's agent tokens); NULL role = derived from kind, as before.
    _ADDED = {"token": ("expires_at TEXT", "role TEXT", "lease_id TEXT"),
              "session": ("role TEXT", "lease_id TEXT", "idle_s INTEGER", "absolute_s INTEGER",
                          "presenting INTEGER")}

    def _migrate(self) -> None:
        for table, cols in self._ADDED.items():
            have = {r[1] for r in self._db.execute(f"PRAGMA table_info({table})")}
            for col in cols:
                if col.split()[0] not in have:
                    self._db.execute(f"ALTER TABLE {table} ADD COLUMN {col}")

    @property
    def db(self) -> sqlite3.Connection:
        return self._db

    @property
    def lock(self) -> threading.Lock:
        return self._lock

    # ---- login rate limit -------------------------------------------------
    def login_allowed(self, client: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._logins.setdefault(client, deque())
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= LOGIN_LIMIT_PER_MIN:
                return False
            q.append(now)
            return True

    # ---- sessions ---------------------------------------------------------
    def create_session(self, user: str, role: str | None = None, lease_id: str | None = None, *,
                       idle_s: int | None = None, absolute_s: int | None = None) -> str:
        """``idle_s`` / ``absolute_s`` override the defaults for this session (operator sessions
        in public mode: 30 min idle, 4 h absolute); NULL in the row means the defaults."""
        sid = secrets.token_urlsafe(32)
        now = self.session_clock()
        idle = idle_s or SESSION_IDLE_S
        with self._lock:
            self._db.execute(
                "INSERT INTO session (session_id,user,csrf,created_at,expires_at,role,lease_id,idle_s,absolute_s)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (_digest(sid), user, "double-submit-cookie", _iso(now),
                 _iso(min(now + idle, now + (absolute_s or SESSION_ABSOLUTE_S))), role, lease_id, idle_s, absolute_s),
            )
        return sid

    def session_record(self, sid: str | None) -> dict | None:
        """{user, role, lease_id} of a live session (sliding idle expiry), else None."""
        if not sid:
            return None
        h = _digest(sid)
        now = self.session_clock()
        with self._lock:
            row = self._db.execute("SELECT user, created_at, expires_at, role, lease_id, idle_s, absolute_s,"
                                   " presenting FROM session WHERE session_id=?", (h,)).fetchone()
            if row is None:
                return None
            user, created, expires = row[0], _ts(row[1]), _ts(row[2])
            idle, absolute = row[5] or SESSION_IDLE_S, row[6] or SESSION_ABSOLUTE_S
            if now > expires or now - created > absolute:
                self._db.execute("DELETE FROM session WHERE session_id=?", (h,))
                return None
            new_exp = min(now + idle, created + absolute)
            self._db.execute("UPDATE session SET expires_at=? WHERE session_id=?", (_iso(new_exp), h))
            return {"user": user, "role": row[3], "lease_id": row[4], "presenting": bool(row[7])}

    def set_presenting(self, sid: str | None, on: bool) -> bool:
        """Presenter mode on an operator session (never a tester's). False when no such session."""
        if not sid:
            return False
        with self._lock:
            return self._db.execute(
                "UPDATE session SET presenting=? WHERE session_id=? AND (role IS NULL OR role='operator')",
                (1 if on else 0, _digest(sid))).rowcount > 0

    def session_user(self, sid: str | None) -> str | None:
        rec = self.session_record(sid)
        return rec["user"] if rec else None

    def drop_lease_sessions(self, lease_id: str) -> int:
        with self._lock:
            return self._db.execute("DELETE FROM session WHERE lease_id=?", (lease_id,)).rowcount

    def drop_operator_sessions(self) -> int:
        """Sign the operator out everywhere: every session that is not a tester's."""
        with self._lock:
            return self._db.execute("DELETE FROM session WHERE role IS NULL OR role='operator'").rowcount

    def drop_session(self, sid: str | None) -> None:
        if sid:
            with self._lock:
                self._db.execute("DELETE FROM session WHERE session_id=?", (_digest(sid),))

    # ---- tokens -----------------------------------------------------------
    def create_token(self, name: str, scope: str, created_by: str, *, expires_at: float | None = None,
                     role: str | None = None, lease_id: str | None = None) -> dict:
        if scope not in SCOPES:
            raise ValueError(f"scope must be one of {SCOPES}")
        value = "shp_" + secrets.token_urlsafe(32)
        tid = "tok_" + secrets.token_hex(6)
        now = time.time()
        with self._lock:
            self._db.execute(
                "INSERT INTO token (token_id,name,token_hash,scopes,created_by,created_at,expires_at,role,lease_id)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (tid, name, _digest(value), scope, created_by, _iso(now),
                 _iso(expires_at) if expires_at else None, role, lease_id),
            )
        return {"id": tid, "name": name, "scope": scope, "token": value, "created": now, "expires": expires_at}

    def list_tokens(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT token_id,name,scopes,expires_at,created_at,created_by,last_used,revoked_at,role,lease_id"
                " FROM token ORDER BY created_at DESC"
            ).fetchall()
        return [{"id": r[0], "name": r[1], "scope": r[2], "created": _ts(r[4]), "created_by": r[5],
                 "last_used": _ts(r[6]), "revoked": _ts(r[7]), "expires": _ts(r[3]), "role": r[8],
                 "lease_id": r[9]} for r in rows]

    def revoke_lease_tokens(self, lease_id: str) -> int:
        with self._lock:
            return self._db.execute("UPDATE token SET revoked_at=? WHERE lease_id=? AND revoked_at IS NULL",
                                    (_iso(time.time()), lease_id)).rowcount

    def revoke_token(self, tid: str) -> bool:
        with self._lock:
            cur = self._db.execute("UPDATE token SET revoked_at=? WHERE token_id=? AND revoked_at IS NULL", (_iso(time.time()), tid))
        return cur.rowcount > 0

    def token_record(self, value: str) -> dict | None:
        """The verified record of a live token (not revoked, not expired), else None. Shape is
        the MCP token verifier's: {id, client_name, scopes, role, lease_id, expires}."""
        now = self.clock()
        with self._lock:
            row = self._db.execute(
                "SELECT token_id,name,scopes,expires_at,role,lease_id FROM token WHERE token_hash=? AND revoked_at IS NULL",
                (_digest(value),)).fetchone()
            if row is None:
                return None
            exp = _ts(row[3])
            if exp is not None and now >= exp:
                return None
            self._db.execute("UPDATE token SET last_used=? WHERE token_id=?", (_iso(now), row[0]))
        return {"id": row[0], "client_name": f"token:{row[1]}", "scopes": row[2].split(","), "role": row[4],
                "lease_id": row[5], "expires": exp}

    def token_principal(self, value: str) -> Principal | None:
        rec = self.token_record(value)
        if rec is None:
            return None
        scope = "propose" if "propose" in rec["scopes"] else "read"
        return Principal("token", rec["client_name"], scope, token_id=rec["id"], role=rec["role"],
                         lease_id=rec["lease_id"])


def admin_hash_file(data_dir: Path) -> Path:
    return data_dir / "admin.hash"


def load_admin_hash(data_dir: Path) -> str | None:
    env = os.environ.get("SHIMPAKU_ADMIN_PASSWORD_HASH")
    if env:
        return env.strip()
    f = admin_hash_file(data_dir)
    return f.read_text().strip() if f.exists() else None


def verify_password(stored_hash: str | None, password: str) -> bool:
    if not stored_hash:
        # Spend comparable time so a missing hash is not distinguishable by timing.
        try:
            _ph.verify(_ph.hash("x"), "y")
        except VerificationError:
            pass
        return False
    try:
        return _ph.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
