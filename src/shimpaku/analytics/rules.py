"""L1/L2 rule tree, hand-written and versioned (PLAN §3: no tree learned from FCS/CRC — those
counters never move on virtio and autonegotiation is always "incomplete", R5 §4).

Each rule reads evidence that moves on this lab and names (fault_class, entity). Rules are
evaluated in order; every matching rule yields a diagnosis, the first is the primary. Changing a
rule means bumping :data:`RULE_TREE_VERSION`; the version is stored on every incident.

fault_class strings match the R5 §6.4 scorer taxonomy.
"""

from __future__ import annotations

from collections.abc import Callable

RULE_TREE_VERSION = "rt-2026.10.24-1"

STUCK = ("ExStart", "Exchange", "Loading", "not-Full")


def _units(units, sle=None, cls=None):
    return [u for u in units if u.get("status") == "bad" and (sle is None or u["sle"] == sle)
            and (cls is None or u.get("classifier") == cls)]


def r_link_down(units, ctx):
    """L1: a fabric port this node saw up now reports oper-down."""
    out = []
    for u in _units(units, "fabric_health"):
        for e in u["evidence"].get("links_down", []):
            out.append(dict(fault_class="link-down", entity=e, layer="L1",
                            why=f"{e} oper-down after being up; carrier lost locally"))
    return out


def r_mtu(units, ctx):
    """L2/L3: OSPF neighbour stuck in ExStart/Exchange while the port is up = MTU mismatch (FAB-04)."""
    out = []
    for u in _units(units, "fabric_health", "Adjacency"):
        for d in u["evidence"].get("ospf_lost", []):
            if d.get("state") in STUCK and not u["evidence"].get("links_down"):
                out.append(dict(fault_class="mtu-mismatch", entity=d["entity"], layer="L2",
                                why=f"OSPF {d['entity']} stuck in {d['state']} with the port up"))
    return out


def r_far_end(units, ctx):
    """L1/L2: neighbour lost (absent/Down/Init) while the local port is up = far-end or one-sided
    failure (FAB-02: carrier does not propagate on virtio)."""
    out = []
    for u in _units(units, "fabric_health", "Adjacency"):
        if u.get("sub_classifier") not in ("Neighbour down", "All neighbours down"):
            continue
        for d in u["evidence"].get("ospf_lost", []) + u["evidence"].get("bfd_lost", []):
            if d.get("state") in STUCK:
                continue
            out.append(dict(fault_class="link-down", entity=d["entity"], layer="L1",
                            why=f"{d['entity']} lost with the local port up: far end or one-sided failure"))
    return out


def r_bgp(units, ctx):
    """Control plane: overlay BGP peer not Established while the underlay is Full (FAB-05)."""
    out = []
    for u in _units(units, "fabric_health", "Adjacency"):
        if u.get("sub_classifier") == "Overlay session down":
            for d in u["evidence"].get("bgp_lost", []):
                out.append(dict(fault_class="bgp-overlay-down", entity=d["entity"], layer="L3",
                                why=f"BGP {d['entity']} not Established; underlay adjacencies Full"))
    return out


def r_flap(units, ctx):
    out = []
    for u in _units(units, "fabric_health", "Interface"):
        for d in u["evidence"].get("carrier_delta", []):
            out.append(dict(fault_class="link-flap", entity=d["entity"], layer="L1",
                            why=f"carrier-transitions +{d['delta']:.0f} in one minute"))
    return out


def r_unreachable_fabric_ok(units, ctx):
    """L3/L4: probe 100% loss while every adjacency in the site is healthy = a policy/filter drop
    on the path (CAM-05) or a data-plane cut the control plane does not see."""
    out = []
    fabric_bad = _units(units, "fabric_health")
    for u in _units(units, "app_reachability", "Unreachable"):
        fc = "acl-shadow-term" if not fabric_bad else "path-down"
        out.append(dict(fault_class=fc, entity=f"probe:{u['client_id']}->{u.get('target')}", layer="L3",
                        why="probe 100% loss" + (" while all adjacencies are healthy" if not fabric_bad
                                                 else " with fabric adjacency loss")))
    return out


def r_lossy(units, ctx):
    out = []
    for u in _units(units, "app_reachability"):
        if u.get("classifier") in ("Loss", "Latency"):
            out.append(dict(fault_class="lossy-link" if u["classifier"] == "Loss" else "app-latency",
                            entity=f"probe:{u['client_id']}->{u.get('target')}", layer="L1",
                            why=f"probe {u['classifier'].lower()} with no device counter moving"))
    return out


def r_resources(units, ctx):
    return [dict(fault_class="host-contention", entity=u["entity_id"], layer="node",
                 why=f"{u['sub_classifier']} above threshold")
            for u in _units(units, "fabric_health", "Resources")]


RULES: list[tuple[str, Callable]] = [
    ("R1-link-down", r_link_down), ("R2-mtu", r_mtu), ("R3-far-end", r_far_end), ("R4-bgp-overlay", r_bgp),
    ("R5-flap", r_flap), ("R6-unreachable", r_unreachable_fabric_ok), ("R7-lossy", r_lossy),
    ("R8-resources", r_resources),
]


def diagnose(units: list[dict], ctx: dict | None = None) -> list[dict]:
    """Ranked, de-duplicated diagnoses; each names the rule and the tree version."""
    seen, out = set(), []
    for name, fn in RULES:
        for d in fn(units, ctx or {}):
            key = (d["fault_class"], d["entity"])
            if key in seen:
                continue
            seen.add(key)
            out.append({**d, "rule": name, "rule_tree": RULE_TREE_VERSION,
                        "minutes": sum(1 for u in units if u.get("status") == "bad")})
    return out
