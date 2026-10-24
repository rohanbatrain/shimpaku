"""Storage: DuckDB `aiops.duckdb` (telemetry, analytics) and SQLite `state.db` (actions, audit).

Stable API — other builders code against these names (docs/build/store-api.md):

    Store(path=None, read_only=False)   .connect() .init() .close()
        .insert_metrics(rows)            rows: iterable of (ts, entity_id, metric, value)
        .insert_events(rows)             rows: iterable of dicts (see EVENT_COLS)
        .insert_rows(table, rows)        generic: list of dicts, columns from the first row
        .upsert_entities(entities) .replace_edges(edges)
        .query(sql, params=None) -> list[dict]     .df(sql, params=None) -> pandas.DataFrame
        .execute(sql, params=None)
        .latest_metrics(entity_prefix=None, metric_prefix=None, max_age_s=120) -> list[dict]
        .events(since=None, node=None, types=None, limit=500) -> list[dict]
        .size_bytes()
    StateDB(path=None)   .connect() .init() .close()
        actions:   action_insert(dict) action_get(id) action_update(id, **fields) action_list(status=None, limit=200)
        approvals: approval_add(action_id, user, decision="approved", note=None) approvals_for(action_id)
        tokens:    token_insert(token_id, name, token_hash, scopes, created_by=None) token_get_by_hash(h)
                   token_list() token_revoke(token_id) token_touch(token_id)
        sessions:  session_insert(session_id, user, csrf, ttl_s) session_get(id) session_delete(id)
        audit:     audit_append(actor, kind, tool, args, result, status="ok") -> dict  (hash-chained)
                   audit_query(since=None, actor=None, tool=None, limit=200) audit_verify() -> (ok, bad_seq)

All timestamps in DuckDB are naive UTC `TIMESTAMP`. SQLite times are ISO-8601 UTC strings.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import duckdb

from shimpaku.config import settings

SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text()
EVENT_COLS = ("ts", "ingest_ts", "source", "node", "entity_id", "client_id", "type", "attrs", "raw")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _j(v: Any) -> str | None:
    if v is None or isinstance(v, str):
        return v
    return json.dumps(v, sort_keys=True, default=str)


class Store:
    """DuckDB wrapper. One read-write process (shimpakud); others open read_only=True."""

    def __init__(self, path: Path | str | None = None, read_only: bool = False):
        self.path = str(path) if path is not None else str(settings.duckdb_path)
        self.read_only = read_only
        self._con: duckdb.DuckDBPyConnection | None = None
        self._lock = threading.RLock()

    # lifecycle
    def connect(self) -> "Store":
        if self._con is None:
            if self.path != ":memory:":
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            self._con = duckdb.connect(self.path, read_only=self.read_only)
        return self

    def init(self) -> "Store":
        self.connect()
        if not self.read_only:
            with self._lock:
                self._con.execute(SCHEMA_SQL)
        return self

    def close(self) -> None:
        with self._lock:
            if self._con is not None:
                self._con.close()
                self._con = None

    def __enter__(self) -> "Store":
        return self.init()

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def con(self) -> duckdb.DuckDBPyConnection:
        return self.connect()._con  # type: ignore[return-value]

    # generic
    def execute(self, sql: str, params: list | tuple | None = None) -> None:
        with self._lock:
            self.con.execute(sql, params or [])

    def query(self, sql: str, params: list | tuple | None = None) -> list[dict]:
        with self._lock:
            cur = self.con.execute(sql, params or [])
            cols = [d[0] for d in cur.description] if cur.description else []
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def df(self, sql: str, params: list | tuple | None = None):
        with self._lock:
            return self.con.execute(sql, params or []).df()

    def insert_rows(self, table: str, rows: list[dict]) -> int:
        if not rows:
            return 0
        cols = list(rows[0].keys())
        vals = [[_j(r.get(c)) if isinstance(r.get(c), (dict, list)) else r.get(c) for c in cols] for r in rows]
        sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
        with self._lock:
            self.con.executemany(sql, vals)
        return len(rows)

    # telemetry
    def insert_metrics(self, rows: Iterable[tuple]) -> int:
        rows = list(rows)
        if not rows:
            return 0
        import pyarrow as pa

        ts, ent, met, val = zip(*rows)
        tbl = pa.table({"ts": pa.array(ts, pa.timestamp("us")), "entity_id": pa.array(ent, pa.string()),
                        "metric": pa.array(met, pa.string()), "value": pa.array(val, pa.float64())})
        with self._lock:
            self.con.register("_m_in", tbl)
            try:
                self.con.execute("INSERT INTO metric_sample SELECT ts, entity_id, metric, value FROM _m_in")
            finally:
                self.con.unregister("_m_in")
        return len(rows)

    def insert_events(self, rows: Iterable[dict]) -> int:
        rows = list(rows)
        if not rows:
            return 0
        vals = [[_j(r.get(c)) if c == "attrs" else r.get(c) for c in EVENT_COLS] for r in rows]
        sql = f"INSERT INTO event ({','.join(EVENT_COLS)}) VALUES ({','.join('?' * len(EVENT_COLS))})"
        with self._lock:
            self.con.executemany(sql, vals)
        return len(rows)

    def upsert_entities(self, entities: Iterable[dict]) -> int:
        n = 0
        with self._lock:
            for e in entities:
                self.con.execute(
                    "INSERT OR REPLACE INTO entity VALUES (?,?,?,?,?)",
                    [e["entity_id"], e.get("kind"), e.get("name"), e.get("parent_id"), _j(e.get("attrs") or {})],
                )
                n += 1
        return n

    def replace_edges(self, edges: Iterable[dict]) -> int:
        edges = list(edges)
        with self._lock:
            self.con.execute("DELETE FROM entity_edge")
            if edges:
                self.con.executemany("INSERT INTO entity_edge VALUES (?,?,?,?)",
                                     [[e["src"], e["dst"], e["relation"], _j(e.get("attrs") or {})] for e in edges])
        return len(edges)

    def latest_metrics(self, entity_prefix: str | None = None, metric_prefix: str | None = None,
                       max_age_s: int = 120) -> list[dict]:
        where, p = ["ts >= ?"], [utcnow() - timedelta(seconds=max_age_s)]
        if entity_prefix:
            where.append("starts_with(entity_id, ?)"); p.append(entity_prefix)
        if metric_prefix:
            where.append("starts_with(metric, ?)"); p.append(metric_prefix)
        return self.query(
            f"SELECT entity_id, metric, arg_max(value, ts) AS value, max(ts) AS ts FROM metric_sample "
            f"WHERE {' AND '.join(where)} GROUP BY entity_id, metric ORDER BY entity_id, metric", p)

    def events(self, since: datetime | None = None, node: str | None = None,
               types: list[str] | None = None, limit: int = 500) -> list[dict]:
        where, p = ["1=1"], []
        if since:
            where.append("ts >= ?"); p.append(since)
        if node:
            where.append("node = ?"); p.append(node)
        if types:
            where.append(f"type IN ({','.join('?' * len(types))})"); p.extend(types)
        p.append(limit)
        return self.query(f"SELECT * FROM event WHERE {' AND '.join(where)} ORDER BY ts DESC LIMIT ?", p)

    def size_bytes(self) -> int:
        if self.path == ":memory:":
            return 0
        f = Path(self.path)
        wal = Path(self.path + ".wal")
        return f.stat().st_size + (wal.stat().st_size if wal.exists() else 0)


STATE_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS action (
    action_id TEXT PRIMARY KEY, kind TEXT NOT NULL, tier TEXT, device TEXT, args TEXT NOT NULL DEFAULT '{}',
    reason TEXT, caller TEXT, incident_id TEXT, status TEXT NOT NULL DEFAULT 'proposed',
    guardrail TEXT, result TEXT, validation TEXT, commit_comment TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS action_status ON action(status);
CREATE TABLE IF NOT EXISTS approval (
    approval_id INTEGER PRIMARY KEY AUTOINCREMENT, action_id TEXT NOT NULL REFERENCES action(action_id),
    user TEXT NOT NULL, decision TEXT NOT NULL, note TEXT, ts TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS token (
    token_id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE, scopes TEXT NOT NULL,
    created_by TEXT, created_at TEXT NOT NULL, last_used TEXT, revoked_at TEXT);
CREATE TABLE IF NOT EXISTS session (
    session_id TEXT PRIMARY KEY, user TEXT NOT NULL, csrf TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT, kind TEXT, tool TEXT,
    args TEXT, result TEXT, status TEXT, prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL);
"""

GENESIS = "0" * 64
_AUDIT_FIELDS = ("seq", "ts", "actor", "kind", "tool", "args", "result", "status", "prev_hash")


def _iso(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="microseconds")


def audit_hash(row: dict) -> str:
    """sha256 over the canonical JSON of the row fields (excluding row_hash)."""
    payload = json.dumps({k: row.get(k) for k in _AUDIT_FIELDS}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


class StateDB:
    """SQLite state.db (WAL). Safe across threads (one connection, a lock)."""

    JSON_COLS = {"args", "result", "validation", "scopes"}

    def __init__(self, path: Path | str | None = None):
        self.path = str(path) if path is not None else str(settings.sqlite_path)
        self._con: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    def connect(self) -> "StateDB":
        if self._con is None:
            if self.path != ":memory:":
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            self._con = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=10)
            self._con.row_factory = sqlite3.Row
        return self

    def init(self) -> "StateDB":
        self.connect()
        with self._lock:
            self._con.executescript(STATE_SCHEMA)
        return self

    def close(self) -> None:
        with self._lock:
            if self._con is not None:
                self._con.close()
                self._con = None

    def __enter__(self) -> "StateDB":
        return self.init()

    def __exit__(self, *exc) -> None:
        self.close()

    def _rows(self, sql: str, p: list | tuple = ()) -> list[dict]:
        with self._lock:
            out = []
            for r in self.connect()._con.execute(sql, p).fetchall():
                d = dict(r)
                for k in self.JSON_COLS & d.keys():
                    if isinstance(d[k], str):
                        try:
                            d[k] = json.loads(d[k])
                        except ValueError:
                            pass
                out.append(d)
            return out

    def _exec(self, sql: str, p: list | tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.connect()._con.execute(sql, p)

    # actions
    def action_insert(self, a: dict) -> dict:
        now = _iso()
        row = {"action_id": a.get("action_id") or f"act-{uuid.uuid4().hex[:12]}", "status": "proposed",
               "created_at": now, "updated_at": now, **{k: v for k, v in a.items() if k != "action_id"}}
        row.setdefault("args", {})
        cols = list(row)
        self._exec(f"INSERT INTO action ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                   [_j(row[c]) if c in self.JSON_COLS else row[c] for c in cols])
        return self.action_get(row["action_id"])  # type: ignore[return-value]

    def action_get(self, action_id: str) -> dict | None:
        r = self._rows("SELECT * FROM action WHERE action_id=?", [action_id])
        return r[0] if r else None

    def action_update(self, action_id: str, **fields: Any) -> dict | None:
        fields["updated_at"] = _iso()
        sets = ",".join(f"{k}=?" for k in fields)
        self._exec(f"UPDATE action SET {sets} WHERE action_id=?",
                   [_j(v) if k in self.JSON_COLS else v for k, v in fields.items()] + [action_id])
        return self.action_get(action_id)

    def action_list(self, status: str | None = None, limit: int = 200) -> list[dict]:
        if status:
            return self._rows("SELECT * FROM action WHERE status=? ORDER BY created_at DESC LIMIT ?", [status, limit])
        return self._rows("SELECT * FROM action ORDER BY created_at DESC LIMIT ?", [limit])

    # approvals
    def approval_add(self, action_id: str, user: str, decision: str = "approved", note: str | None = None) -> dict:
        cur = self._exec("INSERT INTO approval (action_id,user,decision,note,ts) VALUES (?,?,?,?,?)",
                         [action_id, user, decision, note, _iso()])
        return self._rows("SELECT * FROM approval WHERE approval_id=?", [cur.lastrowid])[0]

    def approvals_for(self, action_id: str) -> list[dict]:
        return self._rows("SELECT * FROM approval WHERE action_id=? ORDER BY approval_id", [action_id])

    # tokens (store only a hash of the secret; hashing is the caller's job)
    def token_insert(self, token_id: str, name: str, token_hash: str, scopes: list[str],
                     created_by: str | None = None) -> dict:
        self._exec("INSERT INTO token (token_id,name,token_hash,scopes,created_by,created_at) VALUES (?,?,?,?,?,?)",
                   [token_id, name, token_hash, _j(scopes), created_by, _iso()])
        return self._rows("SELECT * FROM token WHERE token_id=?", [token_id])[0]

    def token_get_by_hash(self, token_hash: str) -> dict | None:
        r = self._rows("SELECT * FROM token WHERE token_hash=? AND revoked_at IS NULL", [token_hash])
        return r[0] if r else None

    def token_list(self) -> list[dict]:
        return self._rows("SELECT token_id,name,scopes,created_by,created_at,last_used,revoked_at FROM token "
                          "ORDER BY created_at DESC")

    def token_revoke(self, token_id: str) -> None:
        self._exec("UPDATE token SET revoked_at=? WHERE token_id=? AND revoked_at IS NULL", [_iso(), token_id])

    def token_touch(self, token_id: str) -> None:
        self._exec("UPDATE token SET last_used=? WHERE token_id=?", [_iso(), token_id])

    # sessions
    def session_insert(self, session_id: str, user: str, csrf: str, ttl_s: int = 43200) -> dict:
        now = datetime.now(timezone.utc)
        self._exec("INSERT INTO session VALUES (?,?,?,?,?)",
                   [session_id, user, csrf, _iso(now), _iso(now + timedelta(seconds=ttl_s))])
        return self.session_get(session_id)  # type: ignore[return-value]

    def session_get(self, session_id: str) -> dict | None:
        r = self._rows("SELECT * FROM session WHERE session_id=? AND expires_at > ?", [session_id, _iso()])
        return r[0] if r else None

    def session_delete(self, session_id: str) -> None:
        self._exec("DELETE FROM session WHERE session_id=?", [session_id])

    # audit (hash chain: row_hash = sha256(canonical(fields incl. prev_hash)); prev_hash = previous row_hash)
    def audit_append(self, actor: str | None, kind: str | None, tool: str | None, args: Any = None,
                     result: Any = None, status: str = "ok") -> dict:
        with self._lock:
            con = self.connect()._con
            con.execute("BEGIN IMMEDIATE")
            try:
                last = con.execute("SELECT seq,row_hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
                row = {"seq": (last["seq"] + 1) if last else 1, "ts": _iso(), "actor": actor, "kind": kind,
                       "tool": tool, "args": _j(args), "result": _j(result), "status": status,
                       "prev_hash": last["row_hash"] if last else GENESIS}
                row["row_hash"] = audit_hash(row)
                con.execute("INSERT INTO audit (seq,ts,actor,kind,tool,args,result,status,prev_hash,row_hash) "
                            "VALUES (:seq,:ts,:actor,:kind,:tool,:args,:result,:status,:prev_hash,:row_hash)", row)
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        return row

    def audit_query(self, since: str | None = None, actor: str | None = None, tool: str | None = None,
                    limit: int = 200) -> list[dict]:
        where, p = ["1=1"], []
        for col, v, op in (("ts", since, ">="), ("actor", actor, "="), ("tool", tool, "=")):
            if v:
                where.append(f"{col} {op} ?"); p.append(v)
        p.append(limit)
        return self._rows(f"SELECT * FROM audit WHERE {' AND '.join(where)} ORDER BY seq DESC LIMIT ?", p)

    def audit_verify(self) -> tuple[bool, int | None]:
        """Walk the chain; return (True, None) or (False, first bad seq)."""
        prev = GENESIS
        with self._lock:
            for r in self.connect()._con.execute("SELECT * FROM audit ORDER BY seq"):
                d = dict(r)
                if d["prev_hash"] != prev or audit_hash(d) != d["row_hash"]:
                    return False, d["seq"]
                prev = d["row_hash"]
        return True, None


__all__ = ["Store", "StateDB", "EVENT_COLS", "audit_hash", "utcnow", "GENESIS"]
