"""Demo data for the explain views (``SHIMPAKU_DEMO=1`` only): extra canned proposals so the
guardrail funnel has every branch, and a canned audit chain whose hashes are computed for real
with the store's ``audit_hash`` (so verify and the tamper test run the production code).

``demo.py`` appends ``extra_actions()`` to its action list and re-exports ``audit_chain`` /
``audit_verify``. Nothing here touches a device or a database.
"""

from __future__ import annotations

import json
import time

DEMO = True
_T0 = time.time()


def _ago(minutes: float) -> float:
    return round(time.time() - minutes * 60, 0)


# (id, kind, device, ifname, caller kind, status, minutes ago, guardrail reason / note, title)
_EXTRA = [
    ("38", "bounce_port", "sw1", "ge-0/0/0", "assistant", "blocked", 50,
     "G1 sw1 ge-0/0/0 is a trunk port (LLDP neighbour r1); only access or unused ports may be bounced",
     "Bounce sw1 ge-0/0/0"),
    ("37", "bounce_port", "sw1", "ge-0/0/2", "agent", "blocked", 140,
     "G2 bounce cap reached: 3/3 on sw1 ge-0/0/2 in 24 h; a human must look", "Bounce sw1 ge-0/0/2"),
    ("36", "add_vlan_to_trunk", "sw1", "ge-0/0/5", "tester", "blocked", 260,
     "G5 sw1 has a pending `commit confirmed`; any commit now would confirm it", "Add VLAN 999 to sw1 ge-0/0/5"),
    ("35", "bounce_port", "leaf1", "ge-0/0/1", "tester", "blocked", 380,
     "T0 tester scope: a tester may propose only add_vlan_to_trunk on sw1 ge-0/0/5 vlan 999",
     "Bounce leaf1 ge-0/0/1"),
    ("34", "bounce_port", "r1", "ge-0/0/3", "agent", "blocked", 700,
     "G0 unexpected argument 'approved' (approvals and overrides are not arguments)", "Bounce r1 ge-0/0/3"),
    ("33", "bounce_port", "sw1", "ge-0/0/3", "agent", "blocked", 1300,
     "G6 no evidence: give an incident_id (or act on a declared test port)", "Bounce sw1 ge-0/0/3"),
    ("32", "rollback", "spine1", None, "agent", "rejected", 1700, "rejected by admin", "Roll back spine1 to rollback 1"),
    ("31", "bounce_port", "sw1", "ge-0/0/4", "assistant", "cancelled", 2400, "cancelled by assistant", "Bounce sw1 ge-0/0/4"),
    ("30", "add_vlan_to_trunk", "sw1", "ge-0/0/5", "tester", "verified", 2900, "healthy for the whole window",
     "Add VLAN 999 to sw1 ge-0/0/5"),
    ("29", "bounce_port", "sw1", "ge-0/0/2", "agent", "recurred", 3600, "symptom back during validation",
     "Bounce sw1 ge-0/0/2"),
    ("28", "rollback", "r2", None, "operator", "failed", 5200,
     "validation failed; Junos rolled back by itself (commit confirmed not confirmed)", "Roll back r2 to rollback 1"),
]
_CALLER = {"assistant": ("assistant", "agent", "assistant"), "agent": ("mcp", "agent", "token:claude-code"),
           "tester": ("dashboard", "tester", "tester"), "operator": ("dashboard", "operator", "admin")}
# the path each extra (and each base demo action) takes through the write path
_PATH = {"blocked": ["blocked"], "rejected": ["awaiting_approval", "rejected"],
         "cancelled": ["awaiting_approval", "cancelled"],
         "verified": ["awaiting_approval", "approved", "running", "validating", "verified"],
         "recurred": ["automatic", "running", "validating", "recurred"],
         "failed": ["awaiting_approval", "approved", "running", "failed"],
         "validating": ["awaiting_approval", "approved", "running", "validating"],
         "awaiting_approval": ["awaiting_approval"], "approved": ["awaiting_approval", "approved"],
         "automatic": ["automatic", "running", "validating", "verified"],
         "fixed_by_operator": ["awaiting_approval", "approved", "running", "failed"]}


def extra_actions() -> list[dict]:
    """View-shaped (like ``demo._actions``) so the Actions list and detail pages show them."""
    out = []
    for aid, kind, dev, ifn, ck, status, mins, note, title in _EXTRA:
        t = _ago(mins)
        blocked = status == "blocked"
        rule = note.split(" ", 1)[0] if blocked else None
        out.append({
            "id": aid, "kind": kind, "tier": "T2" if kind == "rollback" else "T1", "title": title, "device": dev,
            "ifname": ifn, "site": "fabric" if dev.startswith(("leaf", "spine")) else "campus", "status": status,
            "mode": "self-drive" if status == "recurred" else "driver-assist", "proposed_by": _CALLER[ck][2],
            "caller_kind": ck, "note": note, "approved_by": "admin" if status in ("verified", "failed") else None,
            "created_at": t, "incident_id": None, "args": {"device": dev, **({"ifname": ifn} if ifn else {})},
            "reason": "demo proposal", "steps": [{"state": "proposed", "t": t}, {"state": status, "t": t + 60}],
            "guardrails": ([{"id": rule, "name": note, "passed": False, "detail": note}] if blocked else
                           [{"id": g, "name": g, "passed": True} for g in ("G0", "G1", "G5", "G6", "G7")]),
            "policy": {"allowed": not blocked, "status": status if blocked else "awaiting_approval",
                       "reasons": [note] if blocked else []},
            "diff": None, "validation": [{"t": t + 60, "level": "bad" if status in ("blocked", "failed", "recurred") else "info",
                                          "text": note}],
            "verifier": None, "series": None,
        })
    return out


def _kind_of(a: dict) -> str:
    if a.get("caller_kind"):
        return a["caller_kind"]
    by = str(a.get("proposed_by") or "")
    if by == "assistant":
        return "assistant"
    if by.startswith("token:"):
        return "agent"
    if by in ("admin", "operator"):
        return "operator"
    return "other"   # analytics / policy self-drive


def engine_rows(view_rows: list[dict]) -> list[dict]:
    """Demo view rows → the engine's stored shape (caller, policy, timeline), for the funnel."""
    out = []
    for a in view_rows:
        ck = _kind_of(a)
        kind, role, _ = _CALLER.get(ck, ("system", "system", "analytics"))
        status = a.get("status")
        pol = a.get("policy") or {"reasons": [], "checked": [g.get("id") for g in a.get("guardrails") or []]}
        seq = _PATH.get(status, [status])
        tl = [{"ts": a.get("created_at"), "from": "open", "to": s,
               "note": a.get("note") or ""}
              for s in seq]
        out.append({"id": a.get("id"), "kind": a.get("kind"), "status": status,
                    "caller": {"kind": kind, "role": role}, "policy": pol, "timeline": tl,
                    "commits": [{"ts": a.get("created_at")}] if "running" in seq else [],
                    "created": a.get("created_at")})
    return out


# ---- audit chain -----------------------------------------------------------------
_TOOLS = [("mcp:http:tools/call", "sle_get_summary", "agent", "ok"), ("mcp:http:tools/call", "routing_get_health", "agent", "ok"),
          ("mcp:inproc:tools/call", "incident_get", "agent", "ok"), ("rest:propose", "action_propose", "tester", "blocked"),
          ("mcp:http:tools/call", "config_get_commits", "agent", "ok"), ("rest:approve", "action_approve", "operator", "ok"),
          ("executor", "executor", "system", "ok"), ("mcp:http:tools/call", "action_propose_bounce_port", "agent", "refused"),
          ("cli:demo_break", "demo_break", "tester", "ok"), ("mcp:inproc:tools/call", "port_get_config", "agent", "ok")]
_chain: list[dict] | None = None


def _build_chain(n: int = 64) -> list[dict]:
    from shimpaku.store import GENESIS, audit_hash

    rows, prev = [], GENESIS
    for i in range(1, n + 1):
        kind, tool, role, status = _TOOLS[(i * 7) % len(_TOOLS)]
        ts = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(_T0 - (n - i) * 290))
        row = {"seq": i, "ts": ts, "actor": json.dumps({"kind": "demo", "role": role}, sort_keys=True),
               "kind": kind, "tool": tool, "args": json.dumps({"demo": True}), "result": json.dumps({"result_status": status}),
               "status": status, "prev_hash": prev}
        row["row_hash"] = audit_hash(row)
        prev = row["row_hash"]
        rows.append(row)
    return rows


def _rows() -> list[dict]:
    global _chain
    if _chain is None:
        _chain = _build_chain()
    return _chain


async def audit_chain(after: int | None = None, before: int | None = None, limit: int = 20) -> dict:
    rows = [dict(r) for r in reversed(_rows()) if r["seq"] > (after or 0) and r["seq"] < (before or 2**62)]
    return {"demo": DEMO, "rows": rows[:max(1, min(int(limit), 200))], "total": len(_rows())}


async def audit_verify() -> dict:
    from shimpaku.store import GENESIS, audit_hash

    prev = GENESIS
    for r in _rows():
        if r["prev_hash"] != prev or audit_hash(r) != r["row_hash"]:
            return {"demo": DEMO, "ok": False, "first_bad_seq": r["seq"]}
        prev = r["row_hash"]
    return {"demo": DEMO, "ok": True, "rows": len(_rows())}
