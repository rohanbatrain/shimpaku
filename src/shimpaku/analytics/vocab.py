"""The telemetry vocabulary analytics reads — reconciled with docs/build/store-api.md (B-telemetry).

Entity ids (store-api.md): device ``leaf1``; interface ``leaf1:ge-0/0/0`` (logical units too);
adjacencies ``leaf1:ospf:<router-id>``, ``leaf1:bgp:<addr>``, ``leaf1:bfd:<addr>``. Analytics'
own probe ids are ``rpm:<node>:<owner>/<test>``. The node of an entity is its first ``:`` field
(second for ``rpm:``).

Metrics read, mapped by :data:`METRIC_ALIASES` to the short names the engine uses:

| store metric | engine name | healthy |
|---|---|---|
| ``cpu.idle`` (device) | ``cpu_pct`` = 100 − idle | < 80 |
| ``mem.used_pct`` (device) | ``mem_pct`` | < 85 |
| ``if.oper_up`` (interface) | ``oper_up`` | 1 |
| ``if.carrier_transitions``, ``if.in_errors`` | same, counters | delta 0 |
| ``ospf.full`` (1/0) | ``ospf_full`` | 1; a neighbour that disappears = lost |
| ``ospf.state`` (optional code, :data:`OSPF_STATE`) | ``ospf_state`` | 8 |
| ``bfd.up`` | ``bfd_up`` | 1 |
| ``bgp.established`` | ``bgp_established`` | 1 |
| ``ecmp.nexthops`` (optional, not collected yet) | ``ecmp_nexthops`` | = max seen |
"""

from __future__ import annotations

import re

OSPF_STATE = {"Down": 1, "Attempt": 2, "Init": 3, "2Way": 4, "ExStart": 5, "Exchange": 6, "Loading": 7, "Full": 8}
OSPF_NAME = {v: k for k, v in OSPF_STATE.items()}

# collector name -> analytics name (filled in when the store API lands; identity by default)
METRIC_ALIASES: dict[str, str] = {
    "cpu.idle": "cpu_pct", "mem.used_pct": "mem_pct", "if.oper_up": "oper_up",
    "if.carrier_transitions": "carrier_transitions", "if.in_errors": "in_errors",
    "ospf.full": "ospf_full", "ospf.state": "ospf_state", "bfd.up": "bfd_up",
    "bgp.established": "bgp_established", "ecmp.nexthops": "ecmp_nexthops",
}
# store metric -> transform applied after the per-minute aggregate
METRIC_TRANSFORM = {"cpu.idle": lambda v: 100.0 - v}
# per-minute aggregate: min of idle = max CPU busy; max of memory; last of everything else
MINUTE_AGG = {"cpu.idle": "min", "mem.used_pct": "max"}

FABRIC_METRICS = tuple(METRIC_ALIASES)

# physical data ports only; management and logical units never count as fabric links
FABRIC_IF = re.compile(r"^(ge|xe|et|mge)-\d+/\d+/\d+$")

CPU_BAD_PCT = 80.0
MEM_BAD_PCT = 85.0
APP_RTT_MS = 50.0
APP_LOSS_PCT = 1.0

THRESHOLD_VERSION = "sle-2026.10.24-1"


ADJ_KINDS = ("ospf", "bgp", "bfd")


def node_of(entity_id: str) -> str:
    parts = entity_id.split(":")
    return parts[1] if parts[0] == "rpm" and len(parts) > 1 else parts[0]


def ifname_of(entity_id: str) -> str | None:
    parts = entity_id.split(":", 1)
    if len(parts) != 2 or parts[0] in ("rpm", "site", "link") or parts[1].split(":")[0] in ADJ_KINDS:
        return None
    return parts[1]
