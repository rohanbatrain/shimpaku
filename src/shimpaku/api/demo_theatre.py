"""Demo data for theatre mode (``SHIMPAKU_DEMO=1`` only): the demo fixture as a small state machine.

``api/demo.py`` is stateless canned data; theatre mode needs a run that moves when the presenter
presses Break, Propose and Approve. ``install(module)`` wraps the demo module's service functions
so that the demo fixture (``add_vlan_to_trunk`` sw1 ge-0/0/5 VLAN 999) gets service-shaped raw
actions (timeline, commits, validation, policy), ``demo_break`` writes an audit-shaped row and
``port_get_config`` reports VLAN 999 present or missing. Every other call is delegated unchanged.

Everything here is labelled demo (``"demo": true``) and never touches a device. The step timings
after an approval are fixed offsets chosen to look like a lab run; they are demo data, not
measurements. Live mode never imports this module.
"""

from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone
from typing import Any

from shimpaku.actions import fixture

WINDOW_S = 24           # demo validation window (a real tester window is 180 s): fits a short clip
CHECK_EVERY_S = 8
# seconds after approval: executor pick-up, commit confirmed leg, fresh check, confirm, validating
OFFSETS = {"running": 1.0, "commit": 3.2, "health": 5.4, "confirm": 6.6, "validating": 6.9}
CHECKED = ["G0", "G1", "G3", "G4", "G5", "G6", "G7", "G9"]
PROPOSE_MARK = "vlan 999"   # the theatre's Propose prompt; any other question goes to the canned assistant

_T0 = time.time()
_state: dict[str, Any] = {}


def reset() -> None:
    """Back to the canned start: one completed run ~14 minutes ago, VLAN 999 present."""
    t_break = _T0 - 14 * 60
    created = t_break + 31
    a = _new("act-demo-replay", created, {"kind": "mcp", "name": "token:claude-code", "role": "agent"})
    a["approved_at"], a["approved_by"] = created + 19, "admin"
    _state.clear()
    _state.update(breaks=[{"t": t_break, "role": "operator", "kind": "cli"}], seeded=_T0 - 20 * 60,
                  actions={a["id"]: a})


def _new(aid: str, created: float, caller: dict) -> dict:
    return {"id": aid, "created": created, "caller": caller, "approved_at": None, "approved_by": None,
            "ended": None, "blocked": None}


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def _junos(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _raw(a: dict, now: float) -> dict:
    """The action as the real service would return it at ``now`` (only events up to now)."""
    tl: list[dict] = []
    commits: list[dict] = []
    status = "open"

    def ev(t: float, to: str, note: str) -> None:
        nonlocal status
        if t <= now:
            tl.append({"ts": t, "from": status, "to": to, "note": note})
            status = to

    c = a["created"]
    if a["blocked"]:
        ev(c, "blocked", a["blocked"])
    else:
        ev(c, "awaiting_approval", "")
    validation = None
    ap = a["approved_at"]
    if ap and not a["blocked"]:
        ev(ap, "approved", f"approved by {a['approved_by']}")
        ev(ap + OFFSETS["running"], "running", "executor picked up")
        ev(ap + OFFSETS["running"] + 0.2, "running", "lease taken on sw1")
        for leg, k, secs, conf in (("add vlan 999", "commit", 2.21, 2), ("confirm", "confirm", 1.12, None)):
            t = ap + OFFSETS[k]
            if t <= now:
                commits.append({"leg": leg, "comment": f"shimpaku {a['id']} {leg}", "confirm_min": conf,
                                "seconds": secs, "junos_index0_time": _junos(t - 0.3), "ts": t})
                ev(t, "running", f"commit: shimpaku {a['id']} {leg}")
            if k == "commit":
                ev(ap + OFFSETS["health"], "running", "fresh-session health check: {'reachable': True, "
                                                      "'pending_confirm': True, 'ok': True}")
        vs = ap + OFFSETS["validating"]
        window = 180 if a["id"] == "act-demo-replay" else WINDOW_S
        every = 30 if a["id"] == "act-demo-replay" else CHECK_EVERY_S
        if vs <= now:
            ev(vs, "validating", f"confirmed; validating for {window} s")
            checks, t = [], vs + every
            refault = next((b["t"] for b in sorted(_state["breaks"], key=lambda b: b["t"]) if b["t"] >= vs), None)
            while t <= min(now, vs + window):
                ok = refault is None or t < refault
                checks.append({"ts": t, "ok": ok, "vlan_ok": ok})
                if not ok:
                    ev(t, "recurred", "symptom back during validation; re-open triage")
                    break
                if t >= vs + window:
                    break
                t = min(t + every, vs + window)
            if status == "validating" and vs + window <= now:
                ev(vs + window, "verified", "healthy for the whole window")
            validation = {"starts": vs, "ends": vs + window, "checks": checks}
    if a.get("cancelled") and status in ("awaiting_approval", "open"):
        ev(a["cancelled"], "cancelled", "cancelled")
    return {"demo": True, "id": a["id"], "kind": fixture.KIND, "tier": "T1", "device": fixture.DEVICE,
            "ifname": fixture.IFNAME, "args": dict(fixture.ARGS),
            "reason": "VLAN 999 missing from the sw1 trunk; restore it", "caller": a["caller"],
            "incident_id": None, "status": status,
            "policy": {"allowed": not a["blocked"], "tier": "T1",
                       "status": "blocked" if a["blocked"] else "awaiting_approval",
                       "reasons": [a["blocked"]] if a["blocked"] else [], "checked": CHECKED},
            "timeline": tl, "commits": commits, "validation": validation, "error": None,
            "created": a["created"], "updated": tl[-1]["ts"] if tl else a["created"]}


def _present(now: float) -> bool:
    """VLAN 999 on the trunk: the newest of seed / fix commit / break decides."""
    events = [(_state["seeded"], True)] + [(b["t"], False) for b in _state["breaks"]]
    for a in _state["actions"].values():
        if a["approved_at"] and not a["blocked"] and a["approved_at"] + OFFSETS["commit"] <= now:
            events.append((a["approved_at"] + OFFSETS["commit"], True))
    return max((e for e in events if e[0] <= now), key=lambda e: e[0])[1]


def _live(now: float) -> list[dict]:
    return [a for a in _state["actions"].values()
            if _raw(a, now)["status"] in ("open", "awaiting_approval", "approved", "running", "validating")]


def install(mod: Any) -> None:
    """Wrap ``api/demo.py``'s functions in place (called once at the end of that module)."""
    reset()
    orig = {k: getattr(mod, k) for k in ("action_list", "action_get", "action_approve", "action_reject",
                                         "action_cancel", "action_propose", "demo_break", "demo_seed",
                                         "audit_query", "port_get_config", "assistant_ask")}

    def name_of(c: Any) -> dict:
        from shimpaku.actions.engine import Caller
        return Caller.coerce(c).as_dict()

    async def action_list(status: str | None = None) -> dict:
        now = time.time()
        from shimpaku.api import view
        # list rows in the UI shape (the demo module's contract), plus args/created for theatre
        ours = [{**view._action_row(r), "args": r["args"], "created": r["created"], "demo": True}
                for r in (_raw(a, now) for a in sorted(_state["actions"].values(), key=lambda a: -a["created"]))]
        rest = await orig["action_list"](status)
        return {**rest, "actions": [a for a in ours if not status or a["status"] == status] + rest["actions"]}

    async def action_get(action_id: str) -> dict:
        if action_id in _state["actions"]:
            return _raw(_state["actions"][action_id], time.time())
        return await orig["action_get"](action_id)

    async def action_approve(action_id: str, user: Any) -> dict:
        if action_id not in _state["actions"]:
            return await orig["action_approve"](action_id, user)
        c = name_of(user)
        if c.get("kind") != "dashboard" or c.get("role") not in ("operator", "tester"):
            raise PermissionError("approval needs a dashboard session")
        a = _state["actions"][action_id]
        if _raw(a, time.time())["status"] != "awaiting_approval":
            raise ValueError(f"{action_id} is not awaiting approval")
        a["approved_at"], a["approved_by"] = time.time(), c.get("name")
        return await action_get(action_id)

    async def action_cancel(action_id: str, caller: Any) -> dict:
        if action_id not in _state["actions"]:
            return await orig["action_cancel"](action_id, caller)
        a = _state["actions"][action_id]
        if _raw(a, time.time())["status"] not in ("open", "awaiting_approval"):
            raise ValueError(f"{action_id} can no longer be cancelled")
        a["cancelled"] = time.time()
        return await action_get(action_id)

    async def action_reject(action_id: str, user: Any) -> dict:
        if action_id not in _state["actions"]:
            return await orig["action_reject"](action_id, user)
        return await action_cancel(action_id, user)

    def propose_fixture(caller: dict) -> dict:
        now = time.time()
        aid = f"act-demo-{secrets.token_hex(3)}"
        a = _new(aid, now, {k: caller.get(k) for k in ("kind", "name", "role")})
        if _live(now):
            a["blocked"] = "G5 sw1 already has an action in flight"
        _state["actions"][aid] = a
        return _raw(a, now)

    async def action_propose(kind: str, args: dict, reason: str, caller: Any) -> dict:
        if not fixture.is_fixture(kind, args):
            return await orig["action_propose"](kind, args, reason, caller)
        return propose_fixture(name_of(caller))

    async def demo_break(caller: Any = None) -> dict:
        now = time.time()
        changed = _present(now)
        if changed:
            c = name_of(caller) if caller is not None else {}
            _state["breaks"].append({"t": now, "role": c.get("role") or "operator", "kind": c.get("kind")})
        return {"demo": True, "op": "break", "changed": changed,
                "writes": [{"device": fixture.DEVICE, "changed": changed, "diff": "(demo: no device)" if changed else None}]}

    async def demo_seed(caller: Any = None) -> dict:
        now = time.time()
        changed = not _present(now)
        if changed:
            _state["seeded"] = now
        return {"demo": True, "op": "seed", "changed": changed,
                "writes": [{"device": fixture.DEVICE, "changed": changed, "diff": "(demo: no device)" if changed else None}]}

    async def audit_query(limit: int = 50, tool: str | None = None, **kw: Any) -> dict:
        if tool != "demo_break":
            return await orig["audit_query"](limit=limit, tool=tool, **kw)
        rows = [{"ts": _iso(b["t"]), "tool": "demo_break", "status": "changed",
                 "actor": {"role": b["role"], "kind": b["kind"]}} for b in sorted(_state["breaks"], key=lambda b: -b["t"])]
        return {"demo": True, "rows": rows[:limit]}

    async def port_get_config(device: str, ifname: str) -> dict:
        out = await orig["port_get_config"](device, ifname)
        if (device, ifname) == (fixture.DEVICE, fixture.IFNAME):
            out = {**out, "vlan_present": _present(time.time())}
        return out

    async def assistant_ask(question: str, principal: str, context: dict | None = None, llm_key: str | None = None):
        if PROPOSE_MARK not in question.lower() or "propose" not in question.lower():
            async for ev in orig["assistant_ask"](question, principal, context, llm_key=llm_key):
                yield ev
            return
        import asyncio
        yield {"type": "meta", "model": "demo (no LLM)", "demo": True}
        for t in ("port_get_config(sw1, ge-0/0/5)", "topo_get_neighbors(sw1)"):
            await asyncio.sleep(0.25)
            yield {"type": "tool", "name": t.split("(")[0], "call": t, "ms": 180, "ok": True}
        await asyncio.sleep(0.25)
        a = propose_fixture({"kind": "assistant", "name": f"shim:{principal}", "role": "agent"})
        yield {"type": "tool", "name": "action_propose_add_vlan_to_trunk", "call": "action_propose_add_vlan_to_trunk(sw1, ge-0/0/5, 999)",
               "ms": 240, "ok": True}
        for word in f"Proposed {a['id']}: add VLAN 999 back to sw1 ge-0/0/5. Status: {a['status'].replace('_', ' ')}. "\
                    "I cannot approve it; a dashboard user must.".split(" "):
            await asyncio.sleep(0.01)
            yield {"type": "token", "text": word + " "}
        yield {"type": "done"}

    for k, fn in (("action_list", action_list), ("action_get", action_get), ("action_approve", action_approve),
                  ("action_reject", action_reject), ("action_cancel", action_cancel), ("action_propose", action_propose),
                  ("demo_break", demo_break), ("demo_seed", demo_seed), ("audit_query", audit_query),
                  ("port_get_config", port_get_config), ("assistant_ask", assistant_ask)):
        setattr(mod, k, fn)
