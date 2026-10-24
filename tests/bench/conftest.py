"""Virga tests: no live lab. `FakeLab` models per-node config as ordered set-lines, a pending-confirm
flag, and the device lease (through the real shimpaku.lock on a temp DB)."""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bench"))

DATA = Path(__file__).parent / "data"


class FakeLab:
    def __init__(self, nodes: dict[str, list[str]], lock_db: Path, write_ok=None):
        self.cfg = {n: list(v) for n, v in nodes.items()}
        self.nodes = {n: {"mgmt": "192.0.2.1"} for n in nodes}
        self.write_ok = set(write_ok or nodes)
        self.lock_db = lock_db
        self.holder = "virga:test"
        self._leased: set[str] = set()
        self.pending_flag = {n: False for n in nodes}
        self.commits: list[dict] = []
        self.fail_commit_on: set[str] = set()

    def ip(self, node):
        return "192.0.2.1"

    def drop(self, node=None):
        pass

    def guard_write(self, node):
        if node not in self.write_ok:
            raise PermissionError(node)
        if node not in self._leased:
            from virga.lab import NotLeased

            raise NotLeased(node)

    @contextlib.contextmanager
    def leased(self, nodes, purpose="change", check_pending=True, wait=1):
        from shimpaku import lock

        mine = [n for n in sorted(set(nodes)) if n not in self._leased]
        with contextlib.ExitStack() as st:
            for n in mine:
                st.enter_context(lock.device_lock(n, self.holder, purpose, ttl=60, wait=wait, db=self.lock_db))
                if check_pending and self.pending_flag[n]:
                    raise lock.PendingConfirm(n)
                self._leased.add(n)
            try:
                yield
            finally:
                for n in mine:
                    self._leased.discard(n)

    def _apply(self, node, line):
        c = self.cfg[node]
        if line.startswith("set "):
            s = line
            if s not in c:
                c.append(s)
        elif line.startswith("delete "):
            pref = "set " + line[len("delete "):]
            self.cfg[node] = [x for x in c if not (x == pref or x.startswith(pref + " "))]

    def commit(self, node, lines, confirm_min, comment="change", confirm_after=False):
        import time

        self.guard_write(node)
        if node in self.fail_commit_on:
            raise RuntimeError("commit refused")
        before = list(self.cfg[node])
        for ln in lines:
            if "banana" in ln:
                raise RuntimeError("ConfigLoadError: Invalid numeric value: 'banana'")
            self._apply(node, ln)
        changed = before != self.cfg[node]
        self.commits.append({"node": node, "lines": lines, "confirm_min": confirm_min, "confirm_after": confirm_after})
        if changed and confirm_min and not confirm_after:
            self.pending_flag[node] = True
        if confirm_after:
            self.pending_flag[node] = False
        return {"node": node, "lines": lines, "changed": changed, "t_commit": time.time(), "commit_s": 0.0}

    def op(self, node, cmd):
        self.guard_write(node)
        return {"node": node, "op": cmd}

    def config_lines(self, node):
        return list(self.cfg[node])

    def pending(self, node):
        return self.pending_flag[node]


@pytest.fixture
def fake_lab(tmp_path):
    def make(nodes, **kw):
        return FakeLab(nodes, tmp_path / "locks.db", **kw)
    return make
