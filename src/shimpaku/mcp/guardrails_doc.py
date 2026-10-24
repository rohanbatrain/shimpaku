"""The guardrail table served as `lab://policy` (read-only). Code lives in shimpaku.actions.policy."""

GUARDRAILS = [
    {"id": "G0", "rule": "exact argument shape: one known device, one physical port; no ranges, wildcards, "
     "units, interface-range or mgmt names, no unknown keys (e.g. `approved`); write scope from policy",
     "source": "PLAN §5 phase 5 smuggling cases"},
    {"id": "G1", "rule": "bounce/disable only on access ports (or declared unused test ports), never trunk, "
     "fabric or routed; add_vlan_to_trunk only on a declared trunk test port; the more protective of topology role and live role (inheritance expanded, LLDP) wins",
     "source": "R9 §1.2, R7 §4.2"},
    {"id": "G2", "rule": "bounce cap: 3 per port per 24 h, then Open and escalate", "source": "R9 §1.2, R1 C91"},
    {"id": "G3", "rule": "add a VLAN only if it is an allowed demo VLAN and a second device already carries it "
     "(live check, fails closed)",
     "source": "R9 §1.2"},
    {"id": "G4", "rule": "every change is proposed, approved (T2 always by a dashboard user; T1 only "
     "self-drives when toggled) and committed under commit confirmed with a fresh-session check before "
     "confirming; only `rollback 1`", "source": "R9 §1.2, R7 §2.6"},
    {"id": "G5", "rule": "one writer per device: refuse while the device is leased, has a pending commit "
     "confirmed, or already has an action in flight; max actions in flight lab-wide",
     "source": "R7 §4.1, R9 §2.3 (R9 defines no G5; filled here)"},
    {"id": "G6", "rule": "no action without evidence (incident or declared test port) and a reason; none for "
     "client-side faults", "source": "R9 §1.2"},
    {"id": "G7", "rule": "never fix a management-plane fault over the broken path: unreachable device or "
     "mgmt incident → no remote action", "source": "R9 §1.2"},
    {"id": "G8", "rule": "drift is restored from the generator; never roll back the generator's deploy commit",
     "source": "R9 §1.2"},
    {"id": "G9", "rule": "service incidents (RADIUS, DNS, DHCP, NTP) are driver-assist only", "source": "R9 §1.2"},
]
