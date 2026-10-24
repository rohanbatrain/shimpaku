"""Where Virga keeps things. Every path is configurable so the runner can move to user `aiops-bench`.

| env              | default                                   | what                                         |
|------------------|-------------------------------------------|----------------------------------------------|
| VIRGA_STORE      | /home/aiops-bench/store when run as user  | PRIVATE (0700): journal, run records, plans,  |
|                  | aiops-bench, else ~/aiops-bench-store     | ground truth, holdout.yaml, freeze.json       |
| VIRGA_CATALOGUE  | <repo>/bench/catalogue.yaml               | public scenario catalogue                     |
| VIRGA_PUBLISH    | $SHIMPAKU_DATA/bench                      | PUBLIC results the dashboard reads            |
| VIRGA_LOCK_DB    | shimpaku settings.lock_db                 | shared device lease (group shimpaku)          |

Ground truth never goes to VIRGA_PUBLISH while a run is in progress; `virga publish` writes only
finished, scored runs and self-test summaries.
"""

from __future__ import annotations

import getpass
import os
import stat
from pathlib import Path

REPO_BENCH = Path(__file__).resolve().parent.parent


def _settings():
    from shimpaku.config import settings

    return settings


def store() -> Path:
    env = os.environ.get("VIRGA_STORE")
    if env:
        return Path(env).expanduser()
    if getpass.getuser() == "aiops-bench":
        return Path("/home/aiops-bench/store")
    return Path.home() / "aiops-bench-store"


def ensure_private(p: Path) -> Path:
    """Create a 0700 directory and refuse one that others can read (REVIEW B2)."""
    p.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(p.stat().st_mode)
    if mode & 0o077:
        os.chmod(p, 0o700)
    return p


def catalogue() -> Path:
    return Path(os.environ.get("VIRGA_CATALOGUE", REPO_BENCH / "catalogue.yaml")).expanduser()


def holdout() -> Path:
    return Path(os.environ.get("VIRGA_HOLDOUT", store() / "holdout.yaml")).expanduser()


def publish_dir() -> Path:
    env = os.environ.get("VIRGA_PUBLISH")
    if env:
        return Path(env).expanduser()
    return _settings().data / "bench"


def lock_db() -> Path:
    env = os.environ.get("VIRGA_LOCK_DB")
    return Path(env).expanduser() if env else _settings().lock_db


def share_lock_db(db: Path | None = None) -> None:
    """SQLite creates files 0644 whatever the umask; the lease DB must be group-writable (group shimpaku)
    for the daemon, deploy.py and Virga to share it. -wal/-shm inherit the DB file's mode."""
    db = db or lock_db()
    for f in (db, Path(str(db) + "-wal"), Path(str(db) + "-shm")):
        try:
            st = f.stat()
        except FileNotFoundError:
            continue
        if st.st_uid == os.getuid() and (st.st_mode & 0o060) != 0o060:
            os.chmod(f, stat.S_IMODE(st.st_mode) | 0o660)


def device_credentials() -> tuple[str, str]:
    s = _settings()
    return s.device_user, s.device_password


def lab_repo() -> Path:
    return _settings().lab_repo
