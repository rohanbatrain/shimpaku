"""One writer per device, across processes and Unix users.

Junos keeps a single pending `commit confirmed` per box, and any later commit from anyone
confirms it (measured on vJunos 26.2, research R7 §8 / R9 §3). So two writers on one device —
the action executor, deploy.py, the benchmark runner — must never overlap. This module is the
shared lease every writer takes before it opens a configuration session.

The lease lives in a SQLite file that all writer users can open (group-writable directory).
`BEGIN IMMEDIATE` takes SQLite's write lock up front, so check-then-claim is atomic across
processes. Leases expire, so a writer that dies cannot hold a device forever.
"""

from __future__ import annotations

import os
import socket
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

DEFAULT_DB = Path(os.environ.get("SHIMPAKU_LOCK_DB", "/home/lab/shimpaku/locks.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS leases (
    device   TEXT PRIMARY KEY,
    holder   TEXT NOT NULL,
    purpose  TEXT NOT NULL,
    pid      INTEGER NOT NULL,
    host     TEXT NOT NULL,
    acquired REAL NOT NULL,
    expires  REAL NOT NULL
)
"""


class DeviceBusy(Exception):
    """The device stayed leased by someone else for the whole wait."""


class PendingConfirm(Exception):
    """The device has a `commit confirmed` waiting; writing now would confirm someone's change."""


@dataclass(frozen=True)
class Lease:
    device: str
    holder: str
    purpose: str
    expires: float


def _connect(db: Path) -> sqlite3.Connection:
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db, timeout=30, isolation_level=None)  # explicit transactions only
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(SCHEMA)
    return conn


def acquire(device: str, holder: str, purpose: str, ttl: float = 600, wait: float = 120,
            db: Path = DEFAULT_DB, poll: float = 0.5) -> Lease:
    """Claim `device` for `holder`, waiting up to `wait` seconds. Raises DeviceBusy."""
    conn = _connect(db)
    deadline = time.monotonic() + wait
    try:
        while True:
            now = time.time()
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT holder, expires FROM leases WHERE device = ?", (device,)).fetchone()
            if row is None or row[1] <= now or row[0] == holder:
                conn.execute(
                    "INSERT OR REPLACE INTO leases VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (device, holder, purpose, os.getpid(), socket.gethostname(), now, now + ttl),
                )
                conn.execute("COMMIT")
                return Lease(device, holder, purpose, now + ttl)
            conn.execute("ROLLBACK")
            if time.monotonic() >= deadline:
                raise DeviceBusy(f"{device} is held by {row[0]} until {time.ctime(row[1])}")
            time.sleep(poll)
    finally:
        conn.close()


def release(lease: Lease, db: Path = DEFAULT_DB) -> None:
    """Drop the lease if it is still ours; an expired-and-retaken lease is left alone."""
    conn = _connect(db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM leases WHERE device = ? AND holder = ?", (lease.device, lease.holder))
        conn.execute("COMMIT")
    finally:
        conn.close()


def holders(db: Path = DEFAULT_DB) -> list[dict]:
    """Live leases, for the dashboard and the MCP `locks` resource."""
    conn = _connect(db)
    try:
        rows = conn.execute(
            "SELECT device, holder, purpose, pid, host, acquired, expires FROM leases WHERE expires > ?",
            (time.time(),),
        ).fetchall()
    finally:
        conn.close()
    keys = ("device", "holder", "purpose", "pid", "host", "acquired", "expires")
    return [dict(zip(keys, r)) for r in rows]


def pending_confirm(dev) -> bool:
    """True when the box's newest commit is an unconfirmed `commit confirmed` (PyEZ Device)."""
    info = dev.rpc.get_commit_information()
    newest = info.find(".//commit-history")
    if newest is None:
        return False
    # vJunos 26.2 marks it explicitly: <commit-confirmed>rollback pending</commit-confirmed>;
    # the comment "commit confirmed, rollback in Nmins" stays on that entry after it is confirmed,
    # so only the newest entry is read, and the element wins over the text.
    flag = newest.findtext("commit-confirmed")
    if flag is not None:
        return "pending" in flag
    return "rollback in" in (newest.findtext("comment") or "")


@contextmanager
def device_lock(device: str, holder: str, purpose: str, dev=None, **kw) -> Iterator[Lease]:
    """Lease the device; if a PyEZ `dev` is given, also refuse when a confirm is pending."""
    lease = acquire(device, holder, purpose, **kw)
    try:
        if dev is not None and pending_confirm(dev):
            raise PendingConfirm(f"{device} has a pending commit confirmed; refusing to write")
        yield lease
    finally:
        release(lease, db=kw.get("db", DEFAULT_DB))
