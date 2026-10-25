"""Demo data for the live views (``SHIMPAKU_DEMO=1``): the same raw shapes as
``shimpaku.providers_live``, canned and consistent with the ``api/demo.py`` scenario (campus
VLAN 20 fault 35–21 min ago; fabric leaf1–spine2 underlay down for the last 18 min).

Every payload carries ``"demo": true``. Traffic is a smooth deterministic curve per port; the
event tail grows by one canned event every ``EVENT_EVERY_S`` so the stream visibly moves.
"""

from __future__ import annotations

import math
import random
import time

DEMO = True
EVENT_EVERY_S = 9
FABRIC_FAULT_MIN = 18          # minutes ago the leaf1–spine2 underlay went down (demo.py: dips (18, 0))
CAMPUS_FAULT = (35, 21)        # VLAN 20 missing on the sw1 trunk (links stay up)
_LINKS = [("r1", "ge-0/0/1", "sw1", "ge-0/0/0"), ("r1", "ge-0/0/0", "r2", "ge-0/0/0"),
          ("leaf1", "ge-0/0/0", "spine1", "ge-0/0/0"), ("leaf1", "ge-0/0/1", "spine2", "ge-0/0/0"),
          ("leaf2", "ge-0/0/0", "spine1", "ge-0/0/1"), ("leaf2", "ge-0/0/1", "spine2", "ge-0/0/1")]
_NODES = ["r1", "r2", "sw1", "spine1", "spine2", "leaf1", "leaf2"]
_T0 = time.time() - 3 * 3600   # the demo "collector" started three hours ago


def _down(node: str, ifn: str, t: float) -> bool:
    return (node, ifn) in (("leaf1", "ge-0/0/1"), ("spine2", "ge-0/0/0")) and t >= time.time() - FABRIC_FAULT_MIN * 60


def _bps(node: str, ifn: str, direction: str, t: float) -> float | None:
    if _down(node, ifn, t):
        return 0.0
    rnd = random.Random(f"{node}{ifn}{direction}")
    base = rnd.choice([3e5, 1.2e6, 6e6, 2.4e7, 9e7])
    phase = rnd.uniform(0, 6.28)
    wobble = random.Random(f"{node}{ifn}{direction}{int(t // 10)}").uniform(0.92, 1.08)
    v = base * (1 + 0.45 * math.sin(t / 900 + phase)) * wobble
    if node in ("leaf1", "leaf2") and t >= time.time() - FABRIC_FAULT_MIN * 60:
        v *= 1.6 if ifn == "ge-0/0/0" else 1.0   # leaf traffic shifts onto spine1 while spine2's path is down
    return round(v, 1)


async def live_port_rates(at: float | None = None) -> dict:
    t = time.time() if at is None else float(at)
    if t < _T0:
        return {"demo": DEMO, "at": t, "window_s": 90, "ports": {}, "speed_bps": None,
                "speed_reason": "demo: link speed is not collected", "source": "demo: canned"}
    ports: dict[str, dict] = {}
    sample_t = t - (t % 10)
    for a, ai, b, bi in _LINKS:
        for n, i in ((a, ai), (b, bi)):
            ports.setdefault(n, {})[i] = {"in_bps": _bps(n, i, "in", t), "out_bps": _bps(n, i, "out", t), "t": sample_t}
    return {"demo": DEMO, "at": t, "window_s": 90, "ports": ports, "speed_bps": None,
            "speed_reason": "demo: link speed is not collected", "source": "demo: canned"}


def _canned() -> list[tuple[float, str, str | None, str, str, str]]:
    """(minutes ago, node, interface, type, priority, message) — the scenario's story."""
    return [
        (36.0, "sw1", None, "UI_COMMIT", "notice", "User 'lab' requested 'commit' operation (comment: fault lab: drop VLAN 20)"),
        (35.0, "sw1", None, "UI_COMMIT_COMPLETED", "notice", "commit complete"),
        (27.0, "sw1", None, "UI_COMMIT_CONFIRMED", "notice", "User 'shimpaku' requested 'commit confirmed 5' (comment: action 41: add VLAN 20)"),
        (21.0, "sw1", None, "UI_COMMIT_COMPLETED", "notice", "commit confirmed by validator (action 41)"),
        (19.0, "leaf1", None, "UI_COMMIT", "notice", "User 'lab' requested 'commit' operation (comment: filter drop-ospf on ge-0/0/1)"),
        (18.2, "leaf1", "ge-0/0/1", "BFDD_STATE_UP_TO_DOWN", "warning", "BFD Session 10.0.21.2 (IFL ge-0/0/1.0) state Up -> Down LD/RD(17/18) Up time:3d 01:10 Local diag: CtlExpire"),
        (18.1, "leaf1", "ge-0/0/1", "RPD_OSPF_NBRDOWN", "warning", "OSPF neighbor 10.0.21.2 (realm ospf-v2 ge-0/0/1.0 area 0.0.0.0) state changed from Full to Init due to InActiveTimer"),
        (18.0, "spine2", "ge-0/0/0", "RPD_OSPF_NBRDOWN", "warning", "OSPF neighbor 10.0.21.1 (realm ospf-v2 ge-0/0/0.0 area 0.0.0.0) state changed from Full to Init due to InActiveTimer"),
        (12.0, "sw1", "ge-0/0/5", "SNMP_TRAP_LINK_DOWN", "warning", "ifIndex 527, ifAdminStatus up(1), ifOperStatus down(2), ifName ge-0/0/5"),
        (6.0, "leaf1", None, "UI_CFG_AUDIT_SET", "notice", "User 'lab' set: [firewall family inet filter drop-ospf term 1 then] <unconfigured> -> \"discard\""),
    ]


_TICKER = [
    ("leaf2", "ge-0/0/0", "LLDP_NEIGHBOR_UP", "info", "LLDP neighbor spine1 port ge-0/0/1 refreshed"),
    ("spine1", None, "RPD_SCHED_SLIP", "info", "4 sec scheduler slip, user: 0 sec 7429 usec, system: 0 sec, 2010 usec"),
    ("r2", "ge-0/0/0", "RPD_OSPF_NBRUP", "info", "OSPF neighbor 10.0.12.1 (realm ospf-v2 ge-0/0/0.0 area 0.0.0.0) state changed from Loading to Full"),
    ("sw1", "ge-0/0/5", "SNMP_TRAP_LINK_UP", "info", "ifIndex 527, ifAdminStatus up(1), ifOperStatus up(1), ifName ge-0/0/5"),
    ("sw1", "ge-0/0/5", "SNMP_TRAP_LINK_DOWN", "warning", "ifIndex 527, ifAdminStatus up(1), ifOperStatus down(2), ifName ge-0/0/5"),
    ("spine2", None, "CHASSISD_FRU_ONLINE", "info", "FPC 0 online"),
    ("leaf1", "ge-0/0/1", "BFDD_TRAP_SHOP_STATE_DOWN", "error", "local discriminator: 17, new state: down, interface: ge-0/0/1.0"),
]


def _all_events(now: float) -> list[dict]:
    out = []
    for i, (m, node, ifn, typ, pri, msg) in enumerate(_canned()):
        t = now - m * 60
        out.append({"id": i + 1, "t": t, "node": node, "ifn": ifn, "type": typ, "pri": pri, "msg": msg})
    k_first = int(_T0 // EVENT_EVERY_S)   # ticker ids grow with time, so a stream's after_id stays valid
    for k in range(int((now - 5 * 60) // EVENT_EVERY_S), int(now // EVENT_EVERY_S) + 1):
        node, ifn, typ, pri, msg = _TICKER[k % len(_TICKER)]
        out.append({"id": 100 + k - k_first,
                    "t": k * EVENT_EVERY_S, "node": node, "ifn": ifn, "type": typ, "pri": pri, "msg": msg})
    return out


async def live_events(after_id: int | None = None, before_id: int | None = None, limit: int = 200,
                      device: str | None = None, q: str | None = None) -> dict:
    from shimpaku.providers_live import kind_of, severity_of

    now = time.time()
    rows = []
    allev = _all_events(now)
    for e in sorted(allev, key=lambda e: -e["id"]):
        if after_id is not None and e["id"] <= after_id or before_id is not None and e["id"] >= before_id:
            continue
        if device and e["node"] != device:
            continue
        if q and q.lower() not in (e["msg"] + e["type"]).lower():
            continue
        attrs = {"priority": e["pri"], "message": e["msg"], **({"interface-name": e["ifn"]} if e["ifn"] else {})}
        rows.append({"id": e["id"], "t": e["t"], "ingest_t": e["t"] + 0.4, "device": e["node"], "interface": e["ifn"],
                     "type": e["type"], "kind": kind_of(e["type"]), "severity": severity_of(e["type"], attrs),
                     "message": e["msg"], "attrs": attrs})
        if len(rows) >= limit:
            break
    return {"demo": DEMO, "events": rows, "last_id": max(e["id"] for e in allev), "stored": len(allev),
            "first_t": min(e["t"] for e in allev), "source": "demo: canned"}


async def live_range() -> dict:
    now = time.time()
    return {"demo": DEMO, "samples": {"first": _T0, "last": now - now % 10, "interval_s": 10},
            "minutes": {"first": _T0, "last": now - now % 60, "interval_s": 60},
            "retention": "demo: the canned collector started three hours ago"}


async def live_state_at(t: float) -> dict:
    t = float(t)
    if t < _T0:
        return {"demo": DEMO, "t": t, "ports": {}, "ospf": {}, "connected": {}, "verdicts": {},
                "traffic": await live_port_rates(t)}
    ports: dict[str, dict] = {}
    ospf: dict[str, dict] = {}
    for a, ai, b, bi in _LINKS:
        for n, i in ((a, ai), (b, bi)):
            ports.setdefault(n, {})[i] = "up"   # the demo fault is a filter: ports stay up
            if n != "sw1" and not (n == "r1" and i == "ge-0/0/1"):
                ospf.setdefault(n, {})[i] = "not Full" if _down(n, i, t) else "Full"
    bad = t >= time.time() - FABRIC_FAULT_MIN * 60
    campus_bad = time.time() - CAMPUS_FAULT[0] * 60 <= t <= time.time() - CAMPUS_FAULT[1] * 60
    verdicts = {}
    for n in _NODES:
        v_bad = (bad and n in ("leaf1", "spine2")) or (campus_bad and n == "sw1")
        verdicts[n] = {"status": "bad" if v_bad else "good", "minute": t - t % 60,
                       "classifier": ("Adjacency" if n != "sw1" else "VLAN") if v_bad else None,
                       "sub_classifier": ("Neighbour down" if n != "sw1" else "VLAN not carried") if v_bad else None}
    return {"demo": DEMO, "t": t, "ports": ports, "ospf": ospf, "connected": {n: True for n in _NODES},
            "verdicts": verdicts, "traffic": await live_port_rates(t)}


async def live_sle_at(site: str, t: float, window_min: int = 60) -> dict:
    """UI shape directly (``view.sle_summary`` passes it through): the demo SLEs averaged over
    the hour ending at ``t``."""
    from shimpaku.api import demo

    t = float(t)
    now = time.time()
    site = site if site in demo._SLES else "campus"
    sles = []
    for idx, s in enumerate(demo._SLES[site]):
        vals = []
        for m in range(window_min):
            ago = (now - t) / 60 + m
            rnd = random.Random(f"{site}{idx}{int(ago)}")
            v = 99.6 + rnd.uniform(-0.6, 0.4)
            for start, end, depth in s["dips"]:
                if end <= ago <= start:
                    v = depth + rnd.uniform(-2, 2)
            vals.append(max(0.0, min(100.0, v)))
        sles.append({"id": s["id"], "name": s["name"], "value_pct": round(sum(vals) / len(vals), 1),
                     "n": window_min * 10, "good": None, "bad": None, "series": []})
    return {"demo": DEMO, "site": site, "window_min": window_min, "end": t, "sles": sles}
