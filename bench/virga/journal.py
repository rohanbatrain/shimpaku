"""Crash journal: every change Virga has made and not yet proven reverted.

An entry is written BEFORE the inject commit and holds everything `virga recover` needs without the
catalogue: the inverse steps, the pre-inject `display set` snapshot of each touched node, the inject
mode and TTL, and any decoy commits still applied. It is removed only after verify_reverted proves
healthy probes, line-for-line config identity and no pending commit. Writes are atomic (temp file,
fsync, rename) under an flock, so a crash mid-write leaves the previous journal intact.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
from pathlib import Path


class Journal:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextlib.contextmanager
    def _locked(self):
        with open(self.path.with_suffix(".lock"), "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        txt = self.path.read_text()
        return json.loads(txt) if txt.strip() else {}

    def _write(self, data: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w") as fh:
            fh.write(json.dumps(data, indent=1, default=str))
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def entries(self) -> dict:
        with self._locked():
            return self._read()

    def put(self, key: str, entry: dict) -> None:
        with self._locked():
            j = self._read()
            j[key] = entry
            self._write(j)

    def update(self, key: str, **fields) -> None:
        with self._locked():
            j = self._read()
            j.setdefault(key, {}).update(fields)
            self._write(j)

    def append(self, key: str, field: str, item) -> None:
        with self._locked():
            j = self._read()
            j.setdefault(key, {}).setdefault(field, []).append(item)
            self._write(j)

    def pop_item(self, key: str, field: str, match: dict) -> None:
        with self._locked():
            j = self._read()
            if key in j:
                j[key][field] = [x for x in j[key].get(field, []) if any(x.get(k) != v for k, v in match.items())]
                self._write(j)

    def remove(self, key: str) -> None:
        with self._locked():
            j = self._read()
            j.pop(key, None)
            self._write(j)
