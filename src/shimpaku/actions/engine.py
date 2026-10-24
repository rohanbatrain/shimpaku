"""Proposal → policy → approval → executor → validation, with the state machine in SQLite.

States: open → blocked | awaiting_approval | automatic; awaiting_approval → approved (dashboard
user only) | rejected | cancelled; approved/automatic → running → validating → verified |
recurred; any → failed. Validation timers are rows (`next_check`), so a restart resumes them;
an action found `running` at start-up is never re-driven, only observed (`Executor.recover`).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

from shimpaku import devices, lock
from shimpaku.actions import policy as pol
from shimpaku.actions.executor import Executor
from shimpaku.actions.state import LIVE, ActionStore, StateError
from shimpaku.config import settings

log = logging.getLogger("shimpaku.actions")


@dataclass(frozen=True)
class Caller:
    """Who is calling. Built by the transport (MCP middleware, API session), never from tool args."""

    kind: str                 # dashboard | mcp | assistant | system | test
    name: str
    token_id: str | None = None
    transport: str | None = None
    client: str | None = None

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}

    @classmethod
    def coerce(cls, c: Any) -> "Caller":
        if isinstance(c, Caller):
            return c
        if isinstance(c, dict):
            return cls(**{k: c.get(k) for k in ("kind", "name", "token_id", "transport", "client")
                          if c.get(k) is not None} | {"kind": c.get("kind", "unknown"),
                                                       "name": c.get("name", "unknown")})
        return cls(kind="unknown", name=str(c))


class ApprovalRefused(PermissionError):
    pass


FactsFn = Callable[[str, dict], pol.Facts]


class Engine:
    def __init__(self, store: ActionStore | None = None, policy: dict | None = None,
                 facts_fn: FactsFn | None = None, executor: Executor | None = None,
                 incident_lookup: Callable[[str], dict | None] | None = None):
        self.store = store or ActionStore(settings.ensure_data() and settings.sqlite_path)
        self.policy = policy or pol.load_policy(settings.data / "policy.json")
        self.incident_lookup = incident_lookup or (lambda _id: None)
        self.facts_fn = facts_fn or self.live_facts
        self.executor = executor or Executor(self.store, self.policy)
        self._inflight: set[str] = set()
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------ facts
    def live_facts(self, kind: str, args: dict) -> pol.Facts:
        inv = devices.inventory()
        f = pol.Facts(known_devices=set(inv.nodes))
        dev_name = args.get("device") if isinstance(args.get("device"), str) else None
        ifn = args.get("ifname") if isinstance(args.get("ifname"), str) else None
        f.open_actions_total = self.store.count_open()
        if dev_name and dev_name in inv.nodes:
            f.open_actions_device = self.store.count_open(dev_name)
            held = [h for h in lock.holders(db=settings.lock_db) if h["device"] == dev_name]
            f.lease_holder = held[0]["holder"] if held else None
            try:
                dev = devices.open_device(dev_name, timeout=30)
            except Exception:  # noqa: BLE001
                f.reachable = False
                f.pending_confirm = None
                return f
            try:
                f.pending_confirm = lock.pending_confirm(dev)
                top = devices.parse_commits(dev.rpc.get_commit_information(), 1)
                f.newest_commit_log = (top[0].get("log") or "") if top else None
                if ifn and devices.PORT_RE.match(ifn):
                    f.topology_role = inv.port_role(dev_name, ifn) or "unused"
                    f.port = devices.port_facts(dev, ifn)
                    f.bounces_24h = self.store.count_bounces(dev_name, ifn, time.time() - 86400)
                if kind == "rollback":
                    f.rollback_diff = dev.cli("show system rollback 1 compare 0", warning=False) or ""
            finally:
                dev.close()
        inc = args.get("incident_id")
        if isinstance(inc, str):
            f.incident = self.incident_lookup(inc)
        return f

    # ------------------------------------------------------------------ public API (sync)
    def propose(self, kind: str, args: dict, reason: str, caller: Any) -> dict:
        caller = Caller.coerce(caller)
        reason_c = pol.clean_text(reason)
        try:
            facts = self.facts_fn(kind, args if isinstance(args, dict) else {})
        except Exception as e:  # noqa: BLE001
            facts = pol.Facts()
            log.warning("facts collection failed: %s", type(e).__name__)
        decision = pol.evaluate(kind, args if isinstance(args, dict) else {}, reason_c, facts, self.policy)
        clean_args, _ = pol.validate_args(kind, args)
        safe_args = {k: (pol.clean_text(v, 80) if isinstance(v, str) else v) for k, v in
                     (args.items() if isinstance(args, dict) else [])}
        aid = self.store.new_id()
        self.store.insert({
            "id": aid, "kind": str(kind)[:40], "tier": decision.tier,
            "device": clean_args.get("device") if not decision.status == "blocked" else safe_args.get("device"),
            "ifname": clean_args.get("ifname") if not decision.status == "blocked" else safe_args.get("ifname"),
            "args": safe_args, "reason": reason_c, "caller": caller.as_dict(),
            "incident_id": clean_args.get("incident_id"), "status": "open",
            "policy": {**decision.as_dict(), "facts": _facts_summary(facts)},
        })
        a = self.store.transition(aid, decision.status, "; ".join(decision.reasons))
        return self.view(a)

    def approve(self, action_id: str, caller: Any) -> dict:
        """Only a logged-in dashboard user may approve (R7 §2.6: an agent's yes is not a human's)."""
        caller = Caller.coerce(caller)
        if caller.kind != "dashboard":
            self.store.record_approval(action_id, "refused", caller.name, f"{caller.kind}:{caller.transport}")
            raise ApprovalRefused(f"approval refused: caller kind {caller.kind!r} is not a dashboard session")
        a = self.store.transition(action_id, "approved", f"approved by {caller.name}",
                                  expect={"awaiting_approval"})
        self.store.record_approval(action_id, "approved", caller.name, "dashboard")
        return self.view(a)

    def reject(self, action_id: str, caller: Any) -> dict:
        caller = Caller.coerce(caller)
        if caller.kind != "dashboard":
            raise ApprovalRefused("only a dashboard user may reject")
        a = self.store.transition(action_id, "rejected", f"rejected by {caller.name}",
                                  expect={"awaiting_approval"})
        self.store.record_approval(action_id, "rejected", caller.name, "dashboard")
        return self.view(a)

    def cancel(self, action_id: str, caller: Any) -> dict:
        caller = Caller.coerce(caller)
        a = self.store.get(action_id)
        if a is None:
            raise KeyError(action_id)
        if caller.kind != "dashboard" and a["caller"].get("name") != caller.name:
            raise ApprovalRefused("only the proposer or a dashboard user may cancel")
        a = self.store.transition(action_id, "cancelled", f"cancelled by {caller.kind}:{caller.name}",
                                  expect={"open", "awaiting_approval", "approved", "automatic"})
        return self.view(a)

    def get(self, action_id: str) -> dict | None:
        a = self.store.get(action_id)
        return self.view(a) if a else None

    def list(self, status: str | None = None) -> list[dict]:
        return [self.view(a) for a in self.store.list(status)]

    @staticmethod
    def view(a: dict) -> dict:
        out = {k: a.get(k) for k in ("id", "kind", "tier", "device", "ifname", "args", "reason", "caller",
                                    "incident_id", "status", "policy", "timeline", "commits",
                                    "validation", "error", "created", "updated")}
        return out

    # ------------------------------------------------------------------ worker loop
    def tick(self) -> list[str]:
        """One pass: recover orphaned runs, start approved work, run due validations (sync)."""
        done = []
        for a in self.store.list("running"):
            if a["id"] not in self._inflight:
                self.executor.recover(a["id"])
                done.append(a["id"])
        for status in ("approved", "automatic"):
            for a in self.store.list(status):
                if a["id"] not in self._inflight:
                    self._inflight.add(a["id"])
                    try:
                        self.executor.execute(a["id"])
                    except StateError:
                        pass
                    finally:
                        self._inflight.discard(a["id"])
                    done.append(a["id"])
        for a in self.store.due_validations(time.time()):
            self.executor.validate_once(a["id"])
            done.append(a["id"])
        return done

    async def run_forever(self, interval: float = 2.0) -> None:
        """The daemon's executor + validation-timer task."""
        while True:
            try:
                await asyncio.to_thread(self.tick)
            except Exception:  # noqa: BLE001
                log.exception("engine tick failed")
            await asyncio.sleep(interval)

    def start(self) -> asyncio.Task:
        self._task = asyncio.get_running_loop().create_task(self.run_forever())
        return self._task


def _facts_summary(f: pol.Facts) -> dict:
    p = f.port or {}
    return {"topology_role": f.topology_role, "live_role": p.get("live_role"), "admin": p.get("admin"),
            "oper": p.get("oper"), "pending_confirm": f.pending_confirm, "lease_holder": f.lease_holder,
            "reachable": f.reachable, "bounces_24h": f.bounces_24h, "newest_commit_log": f.newest_commit_log,
            "open_actions_device": f.open_actions_device}


_engine: Engine | None = None


def engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = Engine()
    return _engine


def set_engine(e: Engine | None) -> None:
    global _engine
    _engine = e


__all__ = ["Engine", "Caller", "ApprovalRefused", "StateError", "engine", "set_engine", "LIVE"]
