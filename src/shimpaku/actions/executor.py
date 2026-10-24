"""The only code path that writes Junos (CONTRACT "Rules"; R7 §4; PLAN §7 lock protocol).

Per change:
1. lease the device (`shimpaku.lock.device_lock`, cross-process SQLite) — it also refuses when
   the box already has a pending `commit confirmed` (anyone's commit would confirm it);
2. open the Junos candidate in exclusive mode;
3. commit every leg with `commit(confirm=N)` and a comment carrying `shimpaku <action_id>`;
4. health-check from a *fresh* NETCONF session (the change must not have cut our own path);
5. only then confirm with a plain commit. If the executor dies anywhere before step 5, Junos
   rolls the change back by itself after N minutes (R7 measured 73 s for N=1).

PyEZ is synchronous; the engine runs `execute()` in a worker thread.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from shimpaku import devices, lock
from shimpaku.config import settings


class ExecutionError(Exception):
    pass


def _admin(dev, ifname: str) -> str:
    r = dev.rpc.get_interface_information(interface_name=ifname, terse=True)
    return (r.findtext(".//physical-interface/admin-status") or r.findtext(".//admin-status") or "?").strip()


def _newest_commit(dev) -> dict:
    rows = devices.parse_commits(dev.rpc.get_commit_information(), 1)
    return rows[0] if rows else {}


class Executor:
    def __init__(self, store, policy: dict, opener: Callable[[str], Any] = devices.open_device,
                 lock_db=None, sleep: Callable[[float], None] = time.sleep):
        self.store = store
        self.policy = policy
        self.open = opener
        self.lock_db = lock_db or settings.lock_db
        self.sleep = sleep

    # ------------------------------------------------------------------ helpers
    def _commit(self, action: dict, cu, dev, leg: str, confirm: int | None) -> dict:
        comment = f"shimpaku {action['id']} {leg}"
        t0 = time.monotonic()
        cu.commit_check()
        if confirm:
            cu.commit(comment=comment, confirm=confirm)
        else:
            cu.commit(comment=comment)
        top = _newest_commit(dev)
        rec = {"leg": leg, "comment": comment, "confirm_min": confirm, "seconds": round(time.monotonic() - t0, 2),
               "junos_index0_time": top.get("time"), "junos_log": top.get("log"), "ts": time.time()}
        self.store.add_commit(action["id"], rec)
        return rec

    def _fresh_check(self, action: dict, expect_admin: str | None, pre: dict) -> dict:
        """New session, so a change that broke management access fails here, before confirm."""
        dev2 = self.open(action["device"])
        try:
            out = {"reachable": True, "pending_confirm": lock.pending_confirm(dev2),
                   "health": devices.health_snapshot(dev2)}
            if action.get("ifname"):
                out["admin"] = _admin(dev2, action["ifname"])
        finally:
            dev2.close()
        ok = out["pending_confirm"] and all(out["health"][k] >= pre[k] for k in pre)
        if expect_admin is not None:
            ok = ok and out.get("admin") == expect_admin
        out["ok"] = bool(ok)
        return out

    # ------------------------------------------------------------------ entry point
    def execute(self, action_id: str) -> dict:
        from jnpr.junos.utils.config import Config

        a = self.store.transition(action_id, "running", "executor picked up",
                                  expect={"approved", "automatic"})
        kind, device, ifn = a["kind"], a["device"], a.get("ifname")
        n = int(self.policy.get("confirm_minutes", 2))
        if kind == "rerun_deploy":
            return self.store.transition(action_id, "failed", "rerun_deploy needs deploy.py with commit "
                                         "confirmed (R7 §4.2 'to add'); not executed", error="unsupported")
        dev = self.open(device)
        try:
            with lock.device_lock(device, f"shimpaku:{action_id}", f"{kind} {ifn or ''}".strip(),
                                  dev=dev, ttl=900, wait=5, db=self.lock_db):
                self.store.note(action_id, f"lease taken on {device} (lock db {self.lock_db})")
                pre = devices.health_snapshot(dev)
                if ifn:
                    facts = devices.port_facts(dev, ifn)
                    if facts["live_role"] not in ("access", "unused") or facts["interface_range_lines"] \
                            or facts["lldp_neighbor"]:
                        raise ExecutionError(f"G1 re-check at execution failed: live role {facts['live_role']}")
                with Config(dev, mode="exclusive") as cu:
                    if kind == "bounce_port":
                        # Leg 1 is the only confirmed leg. Leg 2 (enable) is a plain commit and is
                        # itself the confirm: a second `commit confirmed` would move the rollback
                        # point to "disabled", so an executor dying after leg 2 would leave the port
                        # down for good (R9 §2.3 chained-confirm trap, measured on vJunos).
                        cu.load(f"set interfaces {ifn} disable", format="set")
                        self._commit(a, cu, dev, "leg1 disable", n)
                        if _admin(dev, ifn) != "down":
                            raise ExecutionError("leg1: port did not go admin down")
                        self.store.note(action_id, f"leg1 committed (confirm {n} min); port admin down")
                        pause = float(self.policy.get("debug_pause_after_leg1_s", 0) or 0)
                        self.sleep(max(3.0, pause))
                        chk = self._fresh_check(a, "down", pre)
                        self.store.note(action_id, f"fresh-session health check: {chk}")
                        if not chk["ok"]:
                            raise ExecutionError(f"health check failed, not confirming; Junos rolls back "
                                                 f"in <= {n} min: {chk}")
                        cu.load(f"delete interfaces {ifn} disable", format="set")
                        self._commit(a, cu, dev, "leg2 enable (confirms leg1)", None)
                        expect = "up"
                        if _admin(dev, ifn) != "up":
                            raise ExecutionError("leg2: port did not come back admin up")
                    else:
                        if kind == "disable_port":
                            cu.load(f"set interfaces {ifn} disable", format="set")
                            self._commit(a, cu, dev, "disable", n)
                            expect = "down"
                        elif kind == "rollback":
                            cu.rollback(rb_id=1)
                            self._commit(a, cu, dev, "rollback 1", n)
                            expect = None
                        else:
                            raise ExecutionError(f"no executor for {kind}")
                        chk = self._fresh_check(a, expect, pre)
                        self.store.note(action_id, f"fresh-session health check: {chk}")
                        if not chk["ok"]:
                            raise ExecutionError(f"health check failed, not confirming; Junos rolls back "
                                                 f"in <= {n} min: {chk}")
                        self._commit(a, cu, dev, "confirm", None)
                if lock.pending_confirm(dev):
                    raise ExecutionError("confirm did not clear the pending commit")
        except lock.PendingConfirm as e:
            return self.store.transition(action_id, "failed", f"G5 refused: {e}", error=str(e))
        except lock.DeviceBusy as e:
            return self.store.transition(action_id, "failed", f"G5 refused: {e}", error=str(e))
        except Exception as e:  # noqa: BLE001
            return self.store.transition(action_id, "failed", f"{type(e).__name__}: {e}", error=str(e))
        finally:
            try:
                dev.close()
            except Exception:  # noqa: BLE001
                pass
        window = int(self.policy.get("validation_window_s", {}).get(kind, 1800))
        now = time.time()
        return self.store.transition(
            action_id, "validating", f"confirmed; validating for {window} s",
            validation={"pre": pre, "expect_admin": expect, "starts": now, "ends": now + window,
                        "checks": []},
            next_check=now + min(window, int(self.policy.get("validation_interval_s", 30))))

    # ------------------------------------------------------------------ validation
    def validate_once(self, action_id: str) -> dict:
        a = self.store.get(action_id)
        v = dict(a["validation"] or {})
        now = time.time()
        try:
            dev = self.open(a["device"])
            try:
                health = devices.health_snapshot(dev)
                admin = _admin(dev, a["ifname"]) if a.get("ifname") else None
                pending = lock.pending_confirm(dev)
            finally:
                dev.close()
        except Exception as e:  # noqa: BLE001
            v["checks"] = [*v.get("checks", []), {"ts": now, "error": type(e).__name__}][-50:]
            return self.store.note(action_id, f"validation probe failed: {type(e).__name__}", validation=v,
                                   next_check=now + int(self.policy.get("validation_interval_s", 30)))
        # Port actions validate the port's symptom (Mist: the symptom is absent for the window);
        # device-wide protocol counts are recorded but other writers (bench, deploy) move them.
        # Whole-device actions (rollback) validate on device health.
        health_ok = all(health[k] >= v["pre"][k] for k in v["pre"])
        if v.get("expect_admin") is not None:
            ok = admin == v["expect_admin"]
        else:
            ok = health_ok
        v["checks"] = [*v.get("checks", []), {"ts": now, "ok": ok, "health": health, "health_ok": health_ok,
                                              "admin": admin, "pending_confirm": pending}][-50:]
        if not ok:
            return self.store.transition(action_id, "recurred", "symptom back during validation; re-open triage",
                                         validation=v, next_check=None)
        if now >= v["ends"]:
            return self.store.transition(action_id, "verified", "healthy for the whole window",
                                         validation=v, next_check=None)
        return self.store.note(action_id, "validation check ok", validation=v,
                               next_check=min(v["ends"], now + int(self.policy.get("validation_interval_s", 30))))

    # ------------------------------------------------------------------ restart recovery
    def recover(self, action_id: str) -> dict:
        """A `running` action found at start-up: its executor died mid-change. Never re-drive it;
        observe whether Junos has rolled it back and record that."""
        a = self.store.get(action_id)
        try:
            dev = self.open(a["device"])
            try:
                pending = lock.pending_confirm(dev)
                admin = _admin(dev, a["ifname"]) if a.get("ifname") else None
                top = _newest_commit(dev)
            finally:
                dev.close()
        except Exception as e:  # noqa: BLE001
            return self.store.note(action_id, f"recovery probe failed: {type(e).__name__}")
        if pending:
            return self.store.note(action_id, "executor restarted; Junos confirm timer still pending")
        # The dead executor's lease would block every other writer until its TTL; it is ours, drop it.
        lock.release(lock.Lease(a["device"], f"shimpaku:{action_id}", "", 0), db=self.lock_db)
        return self.store.transition(
            action_id, "failed",
            f"executor died mid-change; Junos rolled back by itself (newest commit {top.get('time')} by "
            f"{top.get('user')} via {top.get('client')}; admin now {admin})",
            error="interrupted", expect={"running"})
