"""Demo data (``SHIMPAKU_DEMO=1``): the CONTRACT service functions over canned lab-shaped data.

Every payload carries ``"demo": true`` and the UI shows a "Demo data" badge. The scenario: at site
``campus`` VLAN 20 was removed from the sw1 ge-0/0/0 trunk (fault lab, CAM-02 style), DHCP fails for
the servers VLAN; action 41 adds it back under ``commit confirmed 5``. At ``fabric`` leaf1–spine2
underlay is down after a firewall-filter commit; action 42 proposes ``rollback 1`` and waits for
approval. Nothing here touches a device.

It doubles as the reference for the payload shapes the dashboard reads (docs/build/web.md).
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any, AsyncIterator

DEMO = True
_T0 = time.time()


def _ago(minutes: float) -> float:
    return round(time.time() - minutes * 60, 0)


def _series(window_min: int, base: float, dips: list[tuple[int, int, float]], seed: int) -> list[dict]:
    rnd = random.Random(seed)
    step = max(1, window_min // 60)
    out = []
    for i in range(0, window_min, step):
        m = window_min - i  # minutes ago
        v = base + rnd.uniform(-0.6, 0.4)
        for start, end, depth in dips:  # start/end in minutes-ago
            if end <= m <= start:
                v = depth + rnd.uniform(-2, 2)
        out.append({"t": _ago(m), "value": round(max(0.0, min(100.0, v)), 1), "n": rnd.randint(9, 12)})
    return out


_SLES = {
    "campus": [
        {
            "id": "successful-connect", "name": "Successful connect", "domain": "wired", "goal_pct": 99,
            "dips": [(34, 22, 58.0)],
            "classifiers": [
                {"id": "dhcp", "name": "DHCP", "share_pct": 91, "sub": [{"name": "No offer", "share_pct": 88}, {"name": "Nak", "share_pct": 3}]},
                {"id": "arp", "name": "ARP", "share_pct": 9, "sub": [{"name": "Gateway unresolved", "share_pct": 9}]},
                {"id": "auth", "name": "Authentication", "share_pct": 0, "sub": []},
            ],
            "incident_id": "INC-0107",
        },
        {
            "id": "app-reachability", "name": "App reachability", "domain": "wired", "goal_pct": 98,
            "dips": [(33, 22, 71.0)],
            "classifiers": [
                {"id": "gateway", "name": "Gateway", "share_pct": 64, "sub": [{"name": "VLAN not carried", "share_pct": 64}]},
                {"id": "dns", "name": "DNS", "share_pct": 36, "sub": [{"name": "Timeout", "share_pct": 36}]},
                {"id": "http", "name": "HTTP", "share_pct": 0, "sub": []},
            ],
            "incident_id": "INC-0107",
        },
        {
            "id": "switch-health", "name": "Switch health", "domain": "wired", "goal_pct": 99, "dips": [],
            "classifiers": [
                {"id": "unreachable", "name": "Unreachable", "share_pct": 0, "sub": []},
                {"id": "cpu-mem", "name": "CPU / memory", "share_pct": 0, "sub": []},
            ],
        },
        {
            "id": "wan-link-health", "name": "WAN link health", "domain": "wan", "goal_pct": 95,
            "dips": [(52, 47, 88.0), (12, 9, 90.0)],
            "classifiers": [
                {"id": "jitter", "name": "Jitter", "share_pct": 70, "sub": [{"name": "netem r1–r2", "share_pct": 70}]},
                {"id": "latency", "name": "Latency", "share_pct": 30, "sub": []},
                {"id": "loss", "name": "Loss", "share_pct": 0, "sub": []},
            ],
        },
    ],
    "fabric": [
        {
            "id": "fabric-health", "name": "Fabric health", "domain": "fabric", "goal_pct": 99,
            "dips": [(18, 0, 75.0)],
            "classifiers": [
                {"id": "underlay", "name": "Underlay adjacency", "share_pct": 100, "sub": [{"name": "OSPF neighbour down", "share_pct": 62}, {"name": "BFD down", "share_pct": 38}]},
                {"id": "overlay", "name": "EVPN overlay", "share_pct": 0, "sub": []},
                {"id": "interface", "name": "Interface errors", "share_pct": 0, "sub": []},
            ],
            "incident_id": "INC-0108",
        },
        {
            "id": "app-reachability", "name": "App reachability", "domain": "fabric", "goal_pct": 98, "dips": [],
            "classifiers": [
                {"id": "vxlan", "name": "VXLAN forwarding", "share_pct": 0, "sub": []},
                {"id": "gateway", "name": "Gateway", "share_pct": 0, "sub": []},
            ],
        },
    ],
}

_DEVICES = [
    ("r1", "campus", "router", "10.99.0.11"), ("r2", "campus", "router", "10.99.0.12"),
    ("sw1", "campus", "access switch", "10.99.0.13"), ("spine1", "fabric", "spine", "10.99.0.21"),
    ("spine2", "fabric", "spine", "10.99.0.22"), ("leaf1", "fabric", "leaf", "10.99.0.23"),
    ("leaf2", "fabric", "leaf", "10.99.0.24"),
]


async def inventory_list(site: str | None = None) -> dict:
    devices = []
    for i, (name, s, role, ip) in enumerate(_DEVICES):
        if site and s != site:
            continue
        model = "vJunos-router" if role == "router" else ("vJunos-switch" if role != "access switch" else "vJunos-switch")
        status = "degraded" if name in ("leaf1", "spine2") else "connected"
        devices.append({
            "name": name, "site": s, "role": role, "model": model, "version": "26.2R1.7", "mgmt_ip": ip,
            "status": status, "uptime_s": 86400 * 3 + i * 3600, "cpu_pct": 9 + i * 2, "mem_pct": 38 + i,
        })
    return {"demo": DEMO, "devices": devices}


async def sle_get_summary(site: str, window_min: int = 60) -> dict:
    site = site if site in _SLES else "campus"
    sles = []
    for idx, s in enumerate(_SLES[site]):
        dips = [(a * window_min // 60, b * window_min // 60, d) for a, b, d in s["dips"]]
        series = _series(window_min, 99.6, dips, seed=idx + len(site))
        value = round(sum(p["value"] for p in series) / len(series), 1)
        sles.append({
            "id": s["id"], "name": s["name"], "domain": s["domain"], "goal_pct": s["goal_pct"], "value_pct": value,
            "unit": "probe-minutes" if s["domain"] != "fabric" or s["id"] != "fabric-health" else "device-minutes",
            "n": sum(p["n"] for p in series), "series": series, "classifiers": s["classifiers"],
            "incident_id": s.get("incident_id"),
        })
    if site == "campus":
        changes = [
            {"t": _ago(35), "kind": "fault", "text": "VLAN 20 removed from sw1 ge-0/0/0 (fault lab)", "device": "sw1"},
            {"t": _ago(27), "kind": "action", "text": "Action 41 committed (commit confirmed 5)", "ref": "41", "device": "sw1"},
            {"t": _ago(21), "kind": "commit", "text": "Commit confirmed by validator", "device": "sw1"},
        ]
    else:
        changes = [
            {"t": _ago(19), "kind": "commit", "text": "leaf1 commit: firewall filter drop-ospf on ge-0/0/1", "device": "leaf1"},
            {"t": _ago(6), "kind": "action", "text": "Action 42 proposed: rollback 1 on leaf1", "ref": "42", "device": "leaf1"},
        ]
    return {"demo": DEMO, "site": site, "window_min": window_min, "generated_at": time.time(),
            "sites": list(_SLES), "sles": sles, "changes": changes}


_INCIDENTS = {
    "INC-0107": {
        "id": "INC-0107", "title": "DHCP failures on VLAN 20 (servers)", "site": "campus",
        "sle": "successful-connect", "classifier": "dhcp", "status": "verified", "opened_min": 34,
        "impact": {"probe_minutes": 41, "clients": 3, "of_clients": 7}, "action_id": "41",
        "summary": "Trunk sw1 ge-0/0/0 carries VLAN 10 only; r1 still has ge-0/0/1.20. DHCP DISCOVER on VLAN 20 gets no OFFER.",
    },
    "INC-0108": {
        "id": "INC-0108", "title": "leaf1 ↔ spine2 underlay down", "site": "fabric",
        "sle": "fabric-health", "classifier": "underlay", "status": "open", "opened_min": 18,
        "impact": {"device_minutes": 36, "devices": 2, "of_devices": 4}, "action_id": "42",
        "summary": "OSPF and BFD to spine2 dropped one minute after a firewall-filter commit on leaf1. EVPN still forwards via spine1, without redundancy.",
    },
    "INC-0103": {
        "id": "INC-0103", "title": "WAN jitter above threshold r1–r2", "site": "campus",
        "sle": "wan-link-health", "classifier": "jitter", "status": "fixed_by_operator", "opened_min": 300,
        "impact": {"probe_minutes": 12, "clients": 7, "of_clients": 7}, "action_id": None,
        "summary": "netem profile on r1–r2 raised jitter to 38 ms; the operator reset the profile.",
    },
}


def _incident_out(i: dict) -> dict:
    o = {k: v for k, v in i.items() if k != "opened_min"}
    o["opened_at"] = _ago(i["opened_min"])
    return o


async def incident_list(status: str | None = None) -> dict:
    items = [_incident_out(i) for i in _INCIDENTS.values() if not status or i["status"] == status]
    return {"demo": DEMO, "incidents": items}


async def incident_get(incident_id: str) -> dict:
    i = _INCIDENTS.get(incident_id)
    if i is None:
        raise KeyError(incident_id)
    return {"demo": DEMO, **_incident_out(i)}


async def sle_get_blast_radius(incident_id: str) -> dict:
    i = _INCIDENTS.get(incident_id)
    if i is None:
        raise KeyError(incident_id)
    rnd = random.Random(incident_id)
    start = i["opened_min"] + 6
    timeline = []
    for m in range(start + 20, -1, -1):
        total = rnd.randint(9, 12)
        active = i["opened_min"] >= m >= (i["opened_min"] - 12 if i["status"] != "open" else 0)
        timeline.append({"t": _ago(m), "fail": rnd.randint(2, 4) if active else 0, "total": total})
    if incident_id == "INC-0108":
        tree = [{"name": "Underlay adjacency", "share_pct": 100, "children": [
            {"name": "OSPF neighbour down", "share_pct": 62, "children": []},
            {"name": "BFD session down", "share_pct": 38, "children": []}]}]
        affected = [
            {"kind": "device", "id": "leaf1", "name": "leaf1", "detail": "ge-0/0/1 → spine2", "impact_pct": 100, "failure_pct": 50, "minutes": 18},
            {"kind": "device", "id": "spine2", "name": "spine2", "detail": "ge-0/0/0 → leaf1", "impact_pct": 100, "failure_pct": 50, "minutes": 18},
            {"kind": "link", "id": "leaf1:ge-0/0/1", "name": "leaf1 ge-0/0/1 ↔ spine2 ge-0/0/0", "detail": "OSPF area 0", "impact_pct": 100, "failure_pct": 100, "minutes": 18},
        ]
        distribution = [
            {"dimension": "Device", "items": [{"name": "leaf1", "failures": 18, "total": 18}, {"name": "spine2", "failures": 18, "total": 18}, {"name": "spine1", "failures": 0, "total": 18}, {"name": "leaf2", "failures": 0, "total": 18}]},
            {"dimension": "Protocol", "items": [{"name": "OSPF", "failures": 22, "total": 72}, {"name": "BFD", "failures": 14, "total": 72}, {"name": "BGP (EVPN)", "failures": 0, "total": 72}]},
        ]
        evidence = [
            {"kind": "routing", "title": "OSPF neighbour 10.0.21.1 Down", "detail": "leaf1: OSPF to spine2 Down since detection; BFD Down; spine1 adjacency Full.", "tool": "routing_get_health(leaf1)"},
            {"kind": "interface", "title": "ge-0/0/1 one-way traffic", "detail": "admin up, oper up, 0 input packets in 10 min, 0 CRC — not a cable-error pattern.", "tool": "iface_get_errors(leaf1, ge-0/0/1)"},
            {"kind": "change", "title": "Commit 1 minute before", "detail": "set firewall family inet filter protect-re term drop-ospf … applied to ge-0/0/1 by aiops-fault.", "tool": "config_get_commits(leaf1)"},
        ]
    else:
        tree = [{"name": i["classifier"].upper() if i["classifier"] != "jitter" else "Jitter", "share_pct": 91 if incident_id == "INC-0107" else 70, "children": [
            {"name": "No offer", "share_pct": 88, "children": []}, {"name": "Nak", "share_pct": 3, "children": []}]},
            {"name": "ARP", "share_pct": 9, "children": [{"name": "Gateway unresolved", "share_pct": 9, "children": []}]}]
        affected = [
            {"kind": "client", "id": "srv-a", "name": "srv-a", "detail": "VLAN 20 · sw1 ge-0/0/3", "impact_pct": 100, "failure_pct": 100, "minutes": 14},
            {"kind": "client", "id": "srv-b", "name": "srv-b", "detail": "VLAN 20 · sw1 ge-0/0/4", "impact_pct": 100, "failure_pct": 100, "minutes": 14},
            {"kind": "client", "id": "srv-c", "name": "srv-c", "detail": "VLAN 20 · sw1 ge-0/0/5", "impact_pct": 93, "failure_pct": 92, "minutes": 13},
            {"kind": "vlan", "id": "20", "name": "VLAN 20 (servers)", "detail": "trunk sw1 ge-0/0/0 ↔ r1 ge-0/0/1", "impact_pct": 100, "failure_pct": 95, "minutes": 14},
            {"kind": "device", "id": "sw1", "name": "sw1", "detail": "access switch", "impact_pct": 43, "failure_pct": 41, "minutes": 14},
        ]
        distribution = [
            {"dimension": "VLAN", "items": [{"name": "VLAN 20", "failures": 41, "total": 42}, {"name": "VLAN 10", "failures": 0, "total": 56}]},
            {"dimension": "Switch port", "items": [{"name": "ge-0/0/3", "failures": 14, "total": 14}, {"name": "ge-0/0/4", "failures": 14, "total": 14}, {"name": "ge-0/0/5", "failures": 13, "total": 14}, {"name": "ge-0/0/1", "failures": 0, "total": 28}, {"name": "ge-0/0/2", "failures": 0, "total": 28}]},
        ]
        evidence = [
            {"kind": "probe", "title": "DHCP DISCOVER, no OFFER", "detail": "probe_run(vlan=20, dhcp): 3 tries, no OFFER; VLAN 10 control passes in 38 ms.", "tool": "probe_run(vlan20, dhcp)"},
            {"kind": "config", "title": "Trunk carries VLAN 10 only", "detail": "sw1 ge-0/0/0 unit 0 family ethernet-switching vlan members USERS.", "tool": "port_get_config(sw1, ge-0/0/0)"},
            {"kind": "topology", "title": "Peer still expects VLAN 20", "detail": "LLDP: sw1 ge-0/0/0 ↔ r1 ge-0/0/1; r1 has ge-0/0/1.20 vlan-id 20.", "tool": "topo_get_neighbors(sw1)"},
        ]
    return {"demo": DEMO, "incident_id": incident_id, "sle": i["sle"], "classifier": i["classifier"], "tree": tree,
            "affected": affected, "distribution": distribution, "timeline": timeline, "evidence": evidence,
            "scores": {"mutual_information": 0.62, "lift": 7.4}}


def _action41() -> dict:
    committed = _ago(27)
    return {
        "id": "41", "kind": "add_vlan_to_trunk", "tier": "T1", "title": "Add VLAN 20 back to trunk sw1 ge-0/0/0",
        "device": "sw1", "site": "campus", "incident_id": "INC-0107",
        "args": {"device": "sw1", "ifname": "ge-0/0/0", "vlan": "SERVERS"},
        "reason": "DHCP classifier holds 91% of failing probe-minutes on VLAN 20; the trunk to r1 lost VLAN 20.",
        "status": "validating", "mode": "driver-assist", "proposed_by": "assistant", "approved_by": "admin",
        "created_at": _ago(31), "confirm_minutes": 5, "validation_minutes": 30, "committed_at": committed,
        "steps": [
            {"state": "proposed", "t": _ago(31)}, {"state": "approved", "t": _ago(28)},
            {"state": "committed", "t": committed}, {"state": "validating", "t": _ago(26)}, {"state": "verified", "t": None},
        ],
        "guardrails": [
            {"id": "G1", "name": "Port role is trunk to r1 (LLDP r1 ge-0/0/1), not a fabric link", "passed": True},
            {"id": "G2", "name": "VLAN 20 is in use by ≥ 2 clients and defined on r1", "passed": True},
            {"id": "G3", "name": "Change is additive; no VLAN removed", "passed": True},
            {"id": "G5", "name": "Device lease held; no pending commit confirmed", "passed": True},
            {"id": "G7", "name": "Applied with commit confirmed 5; auto-rollback if validation is silent", "passed": True},
        ],
        "diff": "[edit interfaces ge-0/0/0 unit 0 family ethernet-switching vlan]\n-    members USERS;\n+    members [ USERS SERVERS ];",
        "validation": [
            {"t": _ago(34), "level": "bad", "text": "Detected: DHCP classifier 91% of failing minutes on VLAN 20"},
            {"t": _ago(33), "level": "bad", "text": "Probe probe_run(vlan=20, dhcp): DISCOVER sent, no OFFER"},
            {"t": _ago(28), "level": "info", "text": "Approved by admin (assistant proposed, evidence attached)"},
            {"t": committed, "level": "info", "text": "Committed with commit confirmed 5 by the executor"},
            {"t": _ago(26), "level": "good", "text": "Probe VLAN 20: DHCP 41 ms · ARP ok · DNS ok · HTTP 200"},
            {"t": _ago(24), "level": "good", "text": "Commit confirmed; rollback timer cleared"},
            {"t": _ago(21), "level": "good", "text": "Independent verifier: fault cleared — trunk carries VLAN 20"},
            {"t": _ago(3), "level": "pending", "text": "Successful connect 100% for 18 min · verified at 30 min if no recurrence"},
        ],
        "verifier": {"status": "cleared", "detail": "fault lab verifier: trunk carries VLAN 20"},
        "series": _series(60, 99.7, [(34, 26, 52.0)], seed=41),
    }


def _action42() -> dict:
    return {
        "id": "42", "kind": "rollback", "tier": "T2", "title": "Roll back leaf1 to rollback 1",
        "device": "leaf1", "site": "fabric", "incident_id": "INC-0108", "args": {"device": "leaf1", "rollback": 1},
        "reason": "OSPF/BFD to spine2 dropped one minute after commit adding filter term drop-ospf on ge-0/0/1.",
        "status": "awaiting_approval", "mode": "driver-assist", "proposed_by": "token:claude-code", "approved_by": None,
        "created_at": _ago(6), "confirm_minutes": 5, "validation_minutes": 30, "committed_at": None,
        "steps": [{"state": "proposed", "t": _ago(6)}, {"state": "approved", "t": None}, {"state": "committed", "t": None},
                  {"state": "validating", "t": None}, {"state": "verified", "t": None}],
        "guardrails": [
            {"id": "G1", "name": "Target is a fabric leaf; T2 change needs operator approval", "passed": True},
            {"id": "G4", "name": "rollback 1 diff touches only the filter added in the last commit", "passed": True},
            {"id": "G5", "name": "Device lease free; no pending commit confirmed", "passed": True},
            {"id": "G8", "name": "Blast radius limited to leaf1 (1 of 4 fabric devices)", "passed": True},
        ],
        "diff": "[edit firewall family inet filter protect-re]\n-    term drop-ospf {\n-        from protocol ospf;\n-        then discard;\n-    }\n[edit interfaces ge-0/0/1 unit 0 family inet]\n-    filter input protect-re;",
        "validation": [
            {"t": _ago(18), "level": "bad", "text": "Detected: underlay adjacency leaf1 ↔ spine2 down"},
            {"t": _ago(10), "level": "info", "text": "Evidence gathered by token:claude-code (5 tool calls)"},
            {"t": _ago(6), "level": "pending", "text": "Proposed; waiting for operator approval"},
        ],
        "verifier": {"status": "pending", "detail": "runs after commit"},
        "series": _series(60, 99.7, [(18, 0, 74.0)], seed=42),
    }


def _actions() -> list[dict]:
    a40 = {"id": "40", "kind": "bounce_port", "tier": "T1", "title": "Bounce sw1 ge-0/0/2 (err-disabled)", "device": "sw1",
           "site": "campus", "status": "automatic", "mode": "self-drive", "proposed_by": "analytics", "approved_by": "policy",
           "created_at": _ago(190), "incident_id": None}
    a39 = {"id": "39", "kind": "rerun_deploy", "tier": "T1", "title": "Re-run deploy for r2 after timeout", "device": "r2",
           "site": "campus", "status": "fixed_by_operator", "mode": "driver-assist", "proposed_by": "assistant",
           "approved_by": "admin", "created_at": _ago(1500), "incident_id": None}
    return [_action42(), _action41(), a40, a39]


_cancelled: set[str] = set()
_approved: dict[str, str] = {}


def _apply_state(a: dict) -> dict:
    if a["id"] in _cancelled:
        a["status"] = "cancelled"
    elif a["id"] in _approved and a["status"] == "awaiting_approval":
        a["status"], a["approved_by"] = "approved", _approved[a["id"]]
        a["steps"][1]["t"] = time.time()
    return a


async def action_list(status: str | None = None) -> dict:
    items = [_apply_state(a) for a in _actions()]
    items = [{k: v for k, v in a.items() if k not in ("series", "diff", "validation", "guardrails", "steps")}
             for a in items if not status or a["status"] == status]
    return {"demo": DEMO, "actions": items}


async def action_get(action_id: str) -> dict:
    for a in _actions():
        if a["id"] == action_id:
            return {"demo": DEMO, **_apply_state(a)}
    raise KeyError(action_id)


def _name(c) -> str:
    return c.get("name", "?") if isinstance(c, dict) else str(c)


async def action_approve(action_id: str, user) -> dict:
    if isinstance(user, dict) and user.get("kind") != "dashboard":
        raise PermissionError("approval needs a dashboard session")
    user = _name(user)
    a = await action_get(action_id)
    if a["status"] != "awaiting_approval":
        raise ValueError(f"action {action_id} is {a['status']}, not awaiting approval")
    _approved[action_id] = user
    return await action_get(action_id)


async def action_reject(action_id: str, user) -> dict:
    return await action_cancel(action_id, user)


async def action_cancel(action_id: str, caller) -> dict:
    await action_get(action_id)
    _cancelled.add(action_id)
    return await action_get(action_id)


async def action_propose(kind: str, args: dict, reason: str, caller) -> dict:
    return {"demo": DEMO, "id": "43", "kind": kind, "args": args, "reason": reason, "status": "awaiting_approval", "proposed_by": _name(caller)}


async def topo_get_neighbors(device: str | None = None) -> dict:
    edges = [("r1", "ge-0/0/1", "sw1", "ge-0/0/0", "up"), ("r1", "ge-0/0/0", "r2", "ge-0/0/0", "up"),
             ("leaf1", "ge-0/0/0", "spine1", "ge-0/0/0", "up"), ("leaf1", "ge-0/0/1", "spine2", "ge-0/0/0", "down"),
             ("leaf2", "ge-0/0/0", "spine1", "ge-0/0/1", "up"), ("leaf2", "ge-0/0/1", "spine2", "ge-0/0/1", "up")]
    out = [{"a": a, "a_if": ai, "b": b, "b_if": bi, "state": st} for a, ai, b, bi, st in edges if not device or device in (a, b)]
    # raw shapes too (service.topo_get_neighbors), for /api/viz/topology
    roles = {"r1-r2": "routed", "r1-sw1": "trunk"}
    intended, lldp = [], []
    for a, ai, b, bi, st in edges:
        link = f"{a}-{b}"
        for me, mi, peer in ((a, ai, b), (b, bi, a)):
            if device and me != device:
                continue
            intended.append({"device": me, "ifname": mi, "peer": peer, "link": link, "role": roles.get(link, "fabric")})
            if st == "up":
                lldp.append({"device": me, "local_if": mi, "remote_system": peer, "remote_port": link})
    return {"demo": DEMO, "nodes": [d[0] for d in _DEVICES], "edges": out, "intended": intended, "lldp": lldp}


# ---- device views (/api/viz/*): raw service shapes, canned -------------------------------------
_DOWN = ("leaf1", "ge-0/0/1")  # the demo fabric fault: leaf1–spine2 underlay


def _adj(device: str) -> dict:
    ports = {e["ifname"]: e["peer"] for e in _INTENDED if e["device"] == device}
    ospf, bfd = [], []
    for ifn, peer in sorted(ports.items()):
        if (device, ifn) == _DOWN or (device == "spine2" and peer == "leaf1"):
            bfd.append({"neighbor": f"10.0.21.{1 if device == 'leaf1' else 2}", "state": "Down", "interface": f"{ifn}.0"})
            continue
        if device == "sw1" or (device == "r1" and peer == "sw1"):
            continue
        ospf.append({"address": "10.0.0.1", "interface": f"{ifn}.0", "state": "Full", "neighbor_id": peer})
        if device not in ("r1", "r2"):
            bfd.append({"neighbor": "10.0.0.1", "state": "Up", "interface": f"{ifn}.0"})
    return {"device": device, "ospf": ospf, "bgp": [], "bfd": bfd}


_INTENDED: list[dict] = []


async def routing_get_health(device: str | None = None) -> dict:
    if not _INTENDED:
        _INTENDED.extend((await topo_get_neighbors())["intended"])
    names = [device] if device else [d[0] for d in _DEVICES]
    return {"demo": DEMO, "devices": [_adj(n) for n in names]}


async def device_get_health(device: str | None = None, window_min: int = 60) -> dict:
    out = []
    for i, (name, _site, _role, _ip) in enumerate(_DEVICES):
        if device and name != device:
            continue
        bad = 12 if name in ("leaf1", "spine2") else 0
        n = window_min
        out.append({"device": name, "cpu_pct": 9.0 + i * 2, "mem_pct": 38.0 + i, "sampled_at": _ago(0.2),
                    "minutes": {"n": n, "good": n - bad, "bad": bad},
                    "classifiers": [{"classifier": "Adjacency", "sub_classifier": "Neighbour down", "minutes": bad}] if bad else [],
                    "last": {"minute": _ago(1), "status": "bad" if bad else "good", "classifier": "Adjacency" if bad else None,
                             "sub_classifier": "Neighbour down" if bad else None},
                    "ports": {"ge-0/0/0": "up", "ge-0/0/1": "up"}})
    return {"demo": DEMO, "window_min": window_min, "devices": out}


async def port_get_config(device: str, ifname: str) -> dict:
    return {"demo": DEMO, "device": device, "ifname": ifname, "admin": "up", "oper": "down", "vlan_present": True}


async def iface_get_errors(device: str, ifname: str | None = None) -> dict:
    rows = []
    for k in range(3 if device.startswith(("leaf", "spine")) else 2):
        rows.append({"ifname": f"ge-0/0/{k}", "admin": "up", "oper": "down" if (device, f"ge-0/0/{k}") == _DOWN else "up",
                     "description": None, "input_errors": 0, "input_drops": 0, "framing_errors": 0, "fcs_errors": None,
                     "output_errors": 0, "output_drops": 0, "carrier_transitions": 1 + k,
                     "input_packets": 120000 + 7919 * k, "output_packets": 98000 + 4513 * k, "mtu": "1514"})
    return {"demo": DEMO, "device": device, "interfaces": rows}


async def iface_get_history(device: str, window_min: int = 60) -> dict:
    rnd = random.Random(device)
    step = max(1, window_min // 60)
    ifaces = []
    for k in range(2):
        base = rnd.uniform(40_000, 160_000)
        ser = [{"t": _ago(m), "in_bps": round(base * rnd.uniform(.7, 1.3), 1), "out_bps": round(base * rnd.uniform(.5, 1.1), 1),
                "in_pps": round(base / 900, 1), "out_pps": round(base / 1100, 1)} for m in range(window_min, 0, -step)]
        ifaces.append({"ifname": f"ge-0/0/{k}", "series": ser})
    return {"demo": DEMO, "device": device, "window_min": window_min, "step_s": step * 60, "first_sample": _ago(window_min),
            "last_sample": _ago(0.2), "source": "demo: canned", "interfaces": ifaces}


async def audit_verify() -> dict:
    return {"demo": DEMO, "ok": True, "rows": 4}


async def audit_query(limit: int = 50, **_: object) -> dict:
    rows = [
        {"t": _ago(6), "principal": "token:claude-code", "tool": "action_propose_rollback", "args": {"device": "leaf1"}, "result": "ok"},
        {"t": _ago(8), "principal": "token:claude-code", "tool": "config_get_commits", "args": {"device": "leaf1"}, "result": "ok"},
        {"t": _ago(28), "principal": "admin", "tool": "action_approve", "args": {"action_id": "41"}, "result": "ok"},
        {"t": _ago(31), "principal": "assistant", "tool": "action_propose_bounce_port", "args": {}, "result": "refused: G3"},
    ]
    return {"demo": DEMO, "entries": rows[:limit]}


async def bench_results() -> dict:
    return {
        "demo": DEMO, "published": True, "run_id": "demo-run-A", "frozen_tag": "bench-catalogue-v1 (demo)",
        "trials": 105, "null_trials": 22, "classes": 15, "generated_at": _ago(600),
        "systems": [
            {"system": "Shimpaku rules + Shim (diagnose model)", "detect_recall": 0.93, "false_alarm": 0.05, "rca_top1": 0.81, "rca_top3": 0.92, "fix_rate": 0.74, "mttr_s": 212, "unsafe_actions": 0, "pass3": 0.68},
            {"system": "Rules only (no LLM)", "detect_recall": 0.93, "false_alarm": 0.05, "rca_top1": 0.62, "rca_top3": 0.77, "fix_rate": 0.55, "mttr_s": 260, "unsafe_actions": 0, "pass3": 0.55},
            {"system": "B0b: last commit diff", "detect_recall": None, "false_alarm": None, "rca_top1": 0.41, "rca_top3": 0.41, "fix_rate": 0.38, "mttr_s": None, "unsafe_actions": 2, "pass3": 0.41},
            {"system": "LLM only, no tools", "detect_recall": None, "false_alarm": None, "rca_top1": 0.22, "rca_top3": 0.35, "fix_rate": 0.0, "mttr_s": None, "unsafe_actions": 0, "pass3": 0.12},
        ],
        "per_class": [
            {"cls": "missing-vlan-on-trunk", "trials": 8, "rca_top1": 1.0, "fix_rate": 0.88},
            {"cls": "ospf-filter-drop", "trials": 7, "rca_top1": 0.86, "fix_rate": 0.86},
            {"cls": "bfd-timer-mismatch", "trials": 7, "rca_top1": 0.71, "fix_rate": 0.57},
            {"cls": "evpn-vni-mismatch", "trials": 7, "rca_top1": 0.57, "fix_rate": 0.43},
            {"cls": "mtu-mismatch", "trials": 7, "rca_top1": 0.86, "fix_rate": 0.71},
            {"cls": "link-flap", "trials": 6, "rca_top1": 0.83, "fix_rate": 0.67},
            {"cls": "dhcp-relay-removed", "trials": 6, "rca_top1": 0.83, "fix_rate": 0.83},
            {"cls": "wan-jitter (netem)", "trials": 6, "rca_top1": 0.67, "fix_rate": 0.5},
        ],
        "notes": "Demo numbers shaped like the planned scorer output. Real results replace these after run A.",
    }


# ---- assistant ------------------------------------------------------------------
async def demo_seed(caller: Any = None) -> dict:
    """Stand-in for service.demo_seed: no device, nothing written."""
    return {"op": "seed", "changed": True, "writes": [{"device": "sw1", "changed": True, "diff": "(demo: no device)"}]}


async def demo_break(caller: Any = None) -> dict:
    """Stand-in for service.demo_break: no device, nothing written."""
    return {"op": "break", "changed": True, "writes": [{"device": "sw1", "changed": True, "diff": "(demo: no device)"}]}


async def assistant_ask(question: str, principal: str, context: dict | None = None,
                        llm_key: str | None = None) -> AsyncIterator[dict]:
    """Canned streamed answer (no LLM; a tester's per-lease key is accepted and unused): events ``tool`` → ``evidence`` → ``token``… → ``done``."""
    q = question.lower()
    fabric = any(w in q for w in ("fabric", "leaf", "spine", "ospf", "bfd"))
    tools = (["sle_get_summary(fabric, 60)", "routing_get_health(leaf1)", "iface_get_errors(leaf1, ge-0/0/1)", "config_get_commits(leaf1)"]
             if fabric else ["sle_get_summary(campus, 60)", "sle_get_blast_radius(INC-0107)", "port_get_config(sw1, ge-0/0/0)", "probe_run(vlan20, dhcp)"])
    yield {"type": "meta", "model": "demo (no LLM)", "demo": True}
    for t in tools:
        await asyncio.sleep(0.12)
        yield {"type": "tool", "name": t.split("(")[0], "call": t, "ms": random.randint(40, 300), "ok": True}
    if fabric:
        text = ("**Mostly healthy, one degraded path.** leaf1 ↔ spine2 is down at the underlay, so leaf1 is single-homed "
                "through spine1. EVPN VLAN 100 still forwards, but without redundancy. The OSPF drop follows a commit on "
                "leaf1 one minute earlier that added a filter term discarding OSPF on ge-0/0/1. Suggested fix: roll back "
                "leaf1 to rollback 1 under commit confirmed 5 — action 42 is waiting for your approval.")
        ev = [{"title": "Routing · leaf1", "text": "OSPF neighbour 10.0.21.1 Down; BFD Down; spine1 Full", "href": "/root-cause/INC-0108"},
              {"title": "Change · leaf1", "text": "firewall filter term drop-ospf on ge-0/0/1", "href": "/actions/42"}]
    else:
        text = ("**Campus wired connect dipped to 58% for 12 minutes and is back at 100%.** DHCP failures on VLAN 20 "
                "explain 91% of failing probe-minutes; the trunk sw1 ge-0/0/0 had lost VLAN 20. Action 41 added it back "
                "under commit confirmed 5; probes have passed since and the independent verifier reports the fault cleared. "
                "It becomes Verified after 30 minutes with no recurrence.")
        ev = [{"title": "Root cause · INC-0107", "text": "DHCP 91% · VLAN 20 · sw1 ge-0/0/0", "href": "/root-cause/INC-0107"},
              {"title": "Action 41", "text": "add VLAN 20 to trunk · validating", "href": "/actions/41"}]
    for e in ev:
        yield {"type": "evidence", **e}
    for word in text.split(" "):
        await asyncio.sleep(0.018)
        yield {"type": "token", "text": word + " "}
    yield {"type": "done"}

