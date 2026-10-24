"""Baseline B0b, "last commit diff" (REVIEW B3, PLAN §7).

When any health-set probe is degraded, blame the newest commit on any lab node: its node is the root
cause entity, and a keyword rule over its diff guesses the fault class. It uses no analytics and no
LLM. Decoy commits are what keep it honest: without them it would win almost every trial.
"""

from __future__ import annotations

import re

# (regex over the diff text, fault_class); first match wins
RULES = [
    (r"bfd-liveness-detection", "bfd-removed"),
    (r"\bmtu\b", "mtu-mismatch"),
    (r"hello-interval|dead-interval", "ospf-timer-mismatch"),
    (r"\bmetric\b", "ospf-metric-skew"),
    (r"\bpassive\b", "ospf-passive"),
    (r"interface lo0\.0", "ospf-loopback-missing"),
    (r"\bvni\b", "vni-mismatch"),
    (r"protocols bgp|group overlay", "bgp-overlay-down"),
    (r"PROTECT-RE", "mgmt-lockout"),
    (r"policer|filter output", "shaped-link"),
    (r"firewall .*term|discard", "acl-shadow-term"),
    (r"\bdisable\b", "link-down"),
    (r"unit \d+", "vlan-missing"),
    (r"description", "config-drift"),
]
IFACE_RE = re.compile(r"\b((?:ge|xe|et)-\d+/\d+/\d+|lo0)(?:\s+unit\s+(\d+))?")


def classify_diff(node: str, diff: str) -> tuple[str, str]:
    """-> (canonical entity, fault_class) for one commit diff on `node`."""
    fc = next((c for rx, c in RULES if re.search(rx, diff)), "config-change")
    m = IFACE_RE.search(diff)
    if m and fc not in ("bgp-overlay-down", "vni-mismatch", "acl-shadow-term", "mgmt-lockout"):
        ifname = m.group(1) + (f".{m.group(2)}" if m.group(2) and m.group(1) == "lo0" else "")
        return f"intf:{node}:{ifname}", fc
    return f"node:{node}", fc


def newest_commit(lab, nodes: list[str]) -> tuple[str | None, float, str]:
    """Return (node, commit epoch, diff of that commit) for the newest commit across `nodes`."""
    best = (None, 0.0, "")
    for n in nodes:
        try:
            r = lab.dev(n).rpc.get_commit_information()
            top = r.find(".//commit-history")
            if top is None:
                continue
            dt = top.find("date-time")
            sec = next((int(v) for k, v in dt.attrib.items() if k.endswith("seconds")), 0)
            if sec > best[1]:
                best = (n, float(sec), "")
        except Exception:
            lab.drop(n)
    if best[0]:
        try:
            diff = lab.dev(best[0]).cli("show system rollback 1 compare 0", warning=False) or ""
        except Exception:
            diff = ""
        best = (best[0], best[1], diff)
    return best


def b0b_incident(lab, nodes: list[str], degraded: bool, t_now: float, trial_key: str) -> dict | None:
    if not degraded:
        return None
    node, t_commit, diff = newest_commit(lab, nodes)
    if node is None:
        return None
    entity, fc = classify_diff(node, diff)
    return {"incident_id": f"B0b-{trial_key}", "t_detect": t_now, "sle": None,
            "root_causes": [{"entity": entity, "fault_class": fc, "score": 1.0}], "blast_radius": [],
            "evidence": {"newest_commit_node": node, "t_commit": t_commit, "diff": diff[:800]}}
