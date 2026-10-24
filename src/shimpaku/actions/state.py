"""Action, approval and validation-timer persistence, on the store's `StateDB` (state.db).

Mapping onto `shimpaku.store.StateDB` (docs/build/store-api.md, owner B-telemetry):

| ours | StateDB `action` column |
|---|---|
| id | action_id |
| kind, tier, device, reason, incident_id, status, validation | same |
| ifname | inside `args` |
| caller | caller (JSON text) |
| policy decision (+ facts) | guardrail (JSON text) |
| timeline, commits, error | result (JSON) |
| next validation check | validation.next_check |
| `shimpaku <id>` | commit_comment |

Only `shimpakud` writes actions (one process), so read-modify-write transitions are serialised
by one in-process lock. Validation timers are rows, so a restart resumes them.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from shimpaku.store import StateDB

TERMINAL = {"verified", "recurred", "failed", "blocked", "cancelled", "rejected"}
LIVE = {"open", "proposed", "awaiting_approval", "automatic", "approved", "running", "validating"}


def _epoch(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return None


def _loads(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


class StateError(Exception):
    """An illegal state transition (e.g. approving an action that is not awaiting approval)."""


class ActionStore:
    def __init__(self, path: Path | str | None = None, db: StateDB | None = None):
        self.db = db or StateDB(path).init()
        self._mu = threading.RLock()

    # ------------------------------------------------------------------ mapping
    @staticmethod
    def _row(r: dict | None) -> dict | None:
        if r is None:
            return None
        args = r.get("args") or {}
        res = r.get("result") or {}
        val = r.get("validation")
        return {
            "id": r["action_id"], "kind": r["kind"], "tier": r.get("tier"), "device": r.get("device"),
            "ifname": args.get("ifname") if isinstance(args, dict) else None, "args": args,
            "reason": r.get("reason"), "caller": _loads(r.get("caller")) or {},
            "incident_id": r.get("incident_id"), "status": r["status"],
            "policy": _loads(r.get("guardrail")) or {},
            "timeline": res.get("timeline", []), "commits": res.get("commits", []), "error": res.get("error"),
            "validation": val, "next_check": (val or {}).get("next_check") if isinstance(val, dict) else None,
            "commit_comment": r.get("commit_comment"),
            "created": _epoch(r.get("created_at")), "updated": _epoch(r.get("updated_at")),
        }

    def new_id(self) -> str:
        import secrets
        return "act-" + time.strftime("%Y%m%d", time.gmtime()) + "-" + secrets.token_hex(3)

    def insert(self, row: dict) -> dict:
        with self._mu:
            self.db.action_insert({
                "action_id": row["id"], "kind": row["kind"], "tier": row.get("tier"), "device": row.get("device"),
                "args": {**(row.get("args") or {}), **({"ifname": row["ifname"]} if row.get("ifname") else {})},
                "reason": row.get("reason"), "caller": json.dumps(row.get("caller") or {}),
                "incident_id": row.get("incident_id"), "status": row.get("status", "open"),
                "guardrail": json.dumps(row.get("policy") or {}, default=str),
                "result": {"timeline": [], "commits": [], "error": None},
                "validation": None, "commit_comment": f"shimpaku {row['id']}",
            })
        return self.get(row["id"])  # type: ignore[return-value]

    def get(self, action_id: str) -> dict | None:
        return self._row(self.db.action_get(action_id))

    def list(self, status: str | None = None, limit: int = 200) -> list[dict]:
        return [self._row(r) for r in self.db.action_list(status, limit)]  # type: ignore[misc]

    def transition(self, action_id: str, status: str, note: str = "", expect: set[str] | None = None,
                   **fields: Any) -> dict:
        """Move to `status`, appending to the timeline. `expect` guards the from-state.
        Extra fields: validation, next_check, error, commits."""
        with self._mu:
            r = self.db.action_get(action_id)
            if r is None:
                raise KeyError(action_id)
            if expect is not None and r["status"] not in expect:
                raise StateError(f"{action_id} is {r['status']}, not {sorted(expect)}")
            res = dict(r.get("result") or {})
            tl = list(res.get("timeline", []))
            tl.append({"ts": time.time(), "from": r["status"], "to": status, "note": note})
            res["timeline"] = tl
            if "error" in fields:
                res["error"] = fields.pop("error")
            if "commits" in fields:
                res["commits"] = fields.pop("commits")
            upd: dict[str, Any] = {"status": status, "result": res}
            val = fields.pop("validation", r.get("validation"))
            if "next_check" in fields:
                val = dict(val or {})
                val["next_check"] = fields.pop("next_check")
            upd["validation"] = val
            if fields:
                raise TypeError(f"unknown fields {sorted(fields)}")
            self.db.action_update(action_id, **upd)
        return self.get(action_id)  # type: ignore[return-value]

    def note(self, action_id: str, note: str, **fields: Any) -> dict:
        a = self.get(action_id)
        if a is None:
            raise KeyError(action_id)
        return self.transition(action_id, a["status"], note, **fields)

    def add_commit(self, action_id: str, commit: dict) -> None:
        with self._mu:
            a = self.get(action_id)
            if a is None:
                raise KeyError(action_id)
            self.note(action_id, f"commit: {commit.get('comment')}", commits=[*a["commits"], commit])

    def record_approval(self, action_id: str, decision: str, user: str, via: str) -> None:
        self.db.approval_add(action_id, user, decision, note=f"via {via}")

    def approvals(self, action_id: str) -> list[dict]:
        return self.db.approvals_for(action_id)

    def count_open(self, device: str | None = None) -> int:
        return sum(1 for a in self.list(limit=1000) if a["status"] in LIVE and device in (None, a["device"]))

    def count_bounces(self, device: str, ifname: str, since: float) -> int:
        """Bounces that reached Junos (a commit was made), any outcome, in the window (G2)."""
        return sum(1 for a in self.list(limit=1000) if a["kind"] == "bounce_port" and a["device"] == device
                   and a["ifname"] == ifname and (a["created"] or 0) >= since and a["commits"])

    def due_validations(self, now: float) -> list[dict]:
        return [a for a in self.list("validating", limit=1000)
                if a["next_check"] is not None and a["next_check"] <= now]
