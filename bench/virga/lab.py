"""Lab access for the runner: its own NETCONF sessions, the shared device lease, and Junos commits.

Every configuration write goes through `Lab.leased()`, which holds `shimpaku.lock.device_lock` for
each node (sorted, so two writers cannot deadlock), renews the lease every 60 s, and refuses to start
when a node already has someone's `commit confirmed` pending. `Lab.commit()` refuses a node that is
not currently leased or not in the catalogue's `allocation.write`.
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
from pathlib import Path
from typing import Iterator

from virga import paths

NEUTRAL_COMMENT = os.environ.get("VIRGA_COMMIT_COMMENT", "change")
LEASE_TTL_S = 900
LEASE_RENEW_S = 60


class NotLeased(PermissionError):
    pass


class Lab:
    def __init__(self, spec: dict, allow_extra: set[str] | None = None, lock_db: Path | None = None,
                 holder: str | None = None):
        self.nodes = spec["nodes"]
        self.write_ok = set((spec.get("allocation") or {}).get("write", [])) | (allow_extra or set())
        self.lock_db = lock_db or paths.lock_db()
        self.holder = holder or f"virga:{os.getpid()}"
        self.user, self.password = paths.device_credentials()
        self._dev: dict = {}
        self._leased: set[str] = set()

    # ------------------------------------------------------------------ sessions
    def ip(self, node: str) -> str:
        return self.nodes[node]["mgmt"] if node in self.nodes else node

    def _open(self, node: str):
        from jnpr.junos import Device

        d = Device(host=self.ip(node), user=self.user, password=self.password, port=830, gather_facts=False,
                   conn_open_timeout=12)
        d.open()
        d.timeout = 90
        return d

    def dev(self, node: str):
        d = self._dev.get(node)
        if d is not None and d.connected:
            return d
        d = self._open(node)
        self._dev[node] = d
        return d

    def drop(self, node: str | None = None) -> None:
        for n in ([node] if node else list(self._dev)):
            d = self._dev.pop(n, None)
            if d is not None:
                with contextlib.suppress(Exception):
                    d.close()

    # ------------------------------------------------------------------ lease
    def guard_write(self, node: str) -> None:
        if node not in self.write_ok:
            raise PermissionError(f"{node} is not in this runner's write allocation {sorted(self.write_ok)}")
        if node not in self._leased:
            raise NotLeased(f"{node}: refusing to write without holding the device lease")

    @contextlib.contextmanager
    def leased(self, nodes: list[str], purpose: str = "change", check_pending: bool = True,
               wait: float = 600) -> Iterator[None]:
        """Hold shimpaku.lock.device_lock on every node. Pending-confirm refusal when check_pending."""
        from shimpaku import lock

        for n in nodes:
            if n not in self.write_ok:
                raise PermissionError(f"{n} is not in this runner's write allocation {sorted(self.write_ok)}")
        mine = [n for n in sorted(set(nodes)) if n not in self._leased]
        old_umask = os.umask(0o007)  # the lock DB must stay group-writable for group shimpaku
        stop = threading.Event()
        try:
            with contextlib.ExitStack() as stack:
                for n in mine:
                    stack.enter_context(lock.device_lock(n, self.holder, purpose,
                                                         dev=self.dev(n) if check_pending else None,
                                                         ttl=LEASE_TTL_S, wait=wait, db=self.lock_db))
                    self._leased.add(n)
                os.umask(old_umask)
                paths.share_lock_db(self.lock_db)

                def renew():
                    while not stop.wait(LEASE_RENEW_S):
                        for n in mine:
                            with contextlib.suppress(Exception):
                                lock.acquire(n, self.holder, purpose, ttl=LEASE_TTL_S, wait=0, db=self.lock_db)

                t = threading.Thread(target=renew, daemon=True)
                t.start()
                try:
                    yield
                finally:
                    stop.set()
                    for n in mine:
                        self._leased.discard(n)
        finally:
            os.umask(old_umask)

    # ------------------------------------------------------------------ writes
    def commit(self, node: str, lines: list[str], confirm_min: int | None, comment: str = NEUTRAL_COMMENT,
               confirm_after: bool = False) -> dict:
        """Load set-lines in `configure exclusive`, commit check, then commit (confirmed when
        confirm_min). With confirm_after the change is confirmed once the device answers on a NEW
        session (a chained confirm must never be left pending, R9 §2.3)."""
        from jnpr.junos.utils.config import Config

        self.guard_write(node)
        dev = self.dev(node)
        rec: dict = {"node": node, "lines": lines}
        with Config(dev, mode="exclusive") as cu:
            cu.load("\n".join(lines), format="set", merge=True, ignore_warning=True)
            diff = (cu.diff() or "").strip()
            rec["diff"] = diff
            if not diff:
                rec["changed"] = False
                return rec
            cu.commit_check()
            t = time.time()
            if confirm_min:
                cu.commit(confirm=confirm_min, comment=comment, ignore_warning=True)
            else:
                cu.commit(comment=comment, ignore_warning=True)
            rec.update(changed=True, commit_s=round(time.time() - t, 2), t_commit=time.time(), confirm_min=confirm_min)
            if confirm_min and confirm_after:
                fresh = self._open(node)
                try:
                    fresh.rpc.get_system_uptime_information()
                finally:
                    with contextlib.suppress(Exception):
                        fresh.close()
                cu.commit(comment=comment, ignore_warning=True)
                rec["confirmed_s"] = round(time.time() - t, 2)
        return rec

    def op(self, node: str, cmd: str) -> dict:
        self.guard_write(node)  # operational commands that change state (clear ...) count as writes
        t = time.time()
        txt = self.dev(node).cli(cmd, warning=False)
        return {"node": node, "op": cmd, "output": (txt or "").strip()[:300], "s": round(time.time() - t, 2)}

    def config_lines(self, node: str) -> list[str]:
        from lxml import etree

        r = self.dev(node).rpc.get_config(options={"format": "set"})
        txt = r.text if r.text and r.text.strip() else etree.tostring(r, method="text", encoding=str)
        return [ln.strip() for ln in txt.splitlines() if ln.strip()]

    def pending(self, node: str) -> bool:
        from shimpaku import lock

        return lock.pending_confirm(self.dev(node))


def host_load() -> float:
    try:
        return float(Path("/proc/loadavg").read_text().split()[0])
    except OSError:
        return round(os.getloadavg()[0], 2)
