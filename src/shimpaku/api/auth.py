"""Auth for the dashboard and agents (R8 §5, CONTRACT "auth flag").

Modes: ``off`` (no sign-in; the browser user is ``operator``) and ``local`` (one ``admin`` whose
argon2id hash comes from ``SHIMPAKU_ADMIN_PASSWORD_HASH`` or ``<data>/admin.hash``).

Principals:
- a **session user** (browser cookie) may read, propose, cancel and approve;
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

    def caller(self, transport: str = "rest") -> dict:
        """The service-layer caller (shimpaku.actions.engine.Caller fields)."""
        kind = "dashboard" if self.kind == "session" else "mcp"
        return {"kind": kind, "name": self.name, "token_id": self.token_id, "transport": transport}

    @property
    def can_approve(self) -> bool:
        return self.kind == "session"

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
        self._lock = threading.Lock()
        self._logins: dict[str, deque[float]] = {}

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
    def create_session(self, user: str) -> str:
        sid = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._db.execute(
                "INSERT INTO session (session_id,user,csrf,created_at,expires_at) VALUES (?,?,?,?,?)",
                (_digest(sid), user, "double-submit-cookie", _iso(now), _iso(now + SESSION_IDLE_S)),
            )
        return sid

    def session_user(self, sid: str | None) -> str | None:
        if not sid:
            return None
        h = _digest(sid)
        now = time.time()
        with self._lock:
            row = self._db.execute("SELECT user, created_at, expires_at FROM session WHERE session_id=?", (h,)).fetchone()
            if row is None:
                return None
            user, created, expires = row[0], _ts(row[1]), _ts(row[2])
            if now > expires or now - created > SESSION_ABSOLUTE_S:
                self._db.execute("DELETE FROM session WHERE session_id=?", (h,))
                return None
            new_exp = min(now + SESSION_IDLE_S, created + SESSION_ABSOLUTE_S)
            self._db.execute("UPDATE session SET expires_at=? WHERE session_id=?", (_iso(new_exp), h))
            return user

    def drop_session(self, sid: str | None) -> None:
        if sid:
            with self._lock:
                self._db.execute("DELETE FROM session WHERE session_id=?", (_digest(sid),))

    # ---- tokens -----------------------------------------------------------
    def create_token(self, name: str, scope: str, created_by: str) -> dict:
        if scope not in SCOPES:
            raise ValueError(f"scope must be one of {SCOPES}")
        value = "shp_" + secrets.token_urlsafe(32)
        tid = "tok_" + secrets.token_hex(6)
        now = time.time()
        with self._lock:
            self._db.execute(
                "INSERT INTO token (token_id,name,token_hash,scopes,created_by,created_at) VALUES (?,?,?,?,?,?)",
                (tid, name, _digest(value), scope, created_by, _iso(now)),
            )
        return {"id": tid, "name": name, "scope": scope, "token": value, "created": now}

    def list_tokens(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT token_id,name,scopes,NULL,created_at,created_by,last_used,revoked_at FROM token ORDER BY created_at DESC"
            ).fetchall()
        return [{"id": r[0], "name": r[1], "scope": r[2], "created": _ts(r[4]), "created_by": r[5],
                 "last_used": _ts(r[6]), "revoked": _ts(r[7])} for r in rows]

    def revoke_token(self, tid: str) -> bool:
        with self._lock:
            cur = self._db.execute("UPDATE token SET revoked_at=? WHERE token_id=? AND revoked_at IS NULL", (_iso(time.time()), tid))
        return cur.rowcount > 0

    def token_principal(self, value: str) -> Principal | None:
        with self._lock:
            row = self._db.execute(
                "SELECT token_id,name,scopes FROM token WHERE token_hash=? AND revoked_at IS NULL", (_digest(value),)
            ).fetchone()
            if row is None:
                return None
            self._db.execute("UPDATE token SET last_used=? WHERE token_id=?", (_iso(time.time()), row[0]))
        scope = "propose" if "propose" in row[2].split(",") else "read"
        return Principal("token", f"token:{row[1]}", scope, token_id=row[0])


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
