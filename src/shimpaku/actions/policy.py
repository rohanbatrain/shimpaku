"""Guardrails G0–G9 as code. Pure functions over a facts snapshot, so every rule has a unit test.

Sources: R9 §1.2 (G1–G4, G6–G9), R7 §4.1–4.2 (lock, pending confirm, catalogue), PLAN §5 phase 5
(smuggling cases). R9 defines no G5; here G5 is "one writer per device" (R7 §4.1 hazard), and G0
is the argument/scope gate that runs before any device is touched.

A rule never trusts the caller: not the reason text, not the tool annotations, not the role a
topology file claims. Where two sources disagree, the more protective one wins.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shimpaku.devices import DEVICE_RE, PORT_RE

ROLE_RANK = {"unused": 0, "access": 1, "trunk": 2, "routed": 3, "fabric": 4}

CATALOGUE: dict[str, dict[str, Any]] = {
    "bounce_port": {"tier": "T1", "args": {"device": str, "ifname": str}, "optional": {"incident_id": str}},
    "rerun_deploy": {"tier": "T1", "args": {"topology": str}, "optional": {"incident_id": str}},
    "disable_port": {"tier": "T2", "args": {"device": str, "ifname": str}, "optional": {"incident_id": str}},
    "rollback": {"tier": "T2", "args": {"device": str, "rollback_id": int}, "optional": {"incident_id": str}},
}

DEFAULT_POLICY: dict[str, Any] = {
    # driver-assist by default; T1 kinds listed here self-drive (never T2, never services: G4/G9)
    "self_drive": {"bounce_port": False, "rerun_deploy": False},
    "confirm_minutes": 2,
    "validation_window_s": {"bounce_port": 1800, "disable_port": 1800, "rollback": 1800, "rerun_deploy": 1800},
    "validation_interval_s": 30,
    "bounce_cap_24h": 3,                     # G2
    "max_open_actions": 3,                   # blast-radius cap across the lab
    # Lab write scope (build brief): writes only on these ports. Empty dict = no port writes.
    "write_scope_ports": {"spine2": ["ge-0/0/9"],
                          "leaf2": [f"ge-0/0/{i}" for i in range(2, 10)]},
    "write_scope_devices": [],               # devices where whole-device actions (rollback) may run
    # Ports that may be acted on without an incident (lab test ports; G6 otherwise needs evidence)
    "test_ports": {"spine2": ["ge-0/0/9"], "leaf2": [f"ge-0/0/{i}" for i in range(2, 10)]},
    "protected_devices": [],                 # never written (e.g. a box under bench control)
    "service_incident_kinds": ["radius", "dns", "dhcp", "ntp", "service"],   # G9
    "client_incident_kinds": ["client", "client-side", "wrong-credentials"],  # G6
    "generator_commit_marker": "gen_fabric deploy",                           # G8
}


def load_policy(path: Path | None) -> dict[str, Any]:
    pol = json.loads(json.dumps(DEFAULT_POLICY))
    if path is not None and path.exists():
        try:
            pol.update(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError):
            pass  # a broken override file never loosens policy: defaults stand
    return pol


@dataclass
class Facts:
    """What the engine observed just before deciding. Absent facts fail closed."""

    known_devices: set[str] = field(default_factory=set)
    topology_role: str | None = None          # from the lab TOML (intended)
    port: dict | None = None                  # devices.port_facts() (live)
    pending_confirm: bool | None = None       # lock.pending_confirm()
    lease_holder: str | None = None           # someone else's lease on the device
    reachable: bool = True                    # NETCONF answered on a fresh session (G7)
    open_actions_device: int = 0              # non-terminal actions on this device
    open_actions_total: int = 0
    bounces_24h: int = 0                      # executed bounces on this port in 24 h (G2)
    newest_commit_log: str | None = None      # for rollback / G8
    rollback_diff: str | None = None          # for rollback: what would be undone
    incident: dict | None = None              # incident_get() if incident_id was given


@dataclass
class Decision:
    allowed: bool
    tier: str
    status: str                 # blocked | awaiting_approval | automatic
    reasons: list[str]          # why blocked (rule ids first), or why it needs a human
    checked: list[str]          # rules evaluated, in order

    def as_dict(self) -> dict:
        return {"allowed": self.allowed, "tier": self.tier, "status": self.status,
                "reasons": self.reasons, "checked": self.checked}


_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏‪-‮⁦-⁩]")


def clean_text(s: Any, limit: int = 500) -> str:
    """Reason text is stored and shown, never executed or put in config. Normalised so that
    look-alike characters (granite's U+2011 hyphen, R7 §1.3) cannot pass as a port name."""
    s = unicodedata.normalize("NFKC", str(s or ""))
    return _CTRL.sub(" ", s)[:limit].strip()


def validate_args(kind: str, args: Any) -> tuple[dict, list[str]]:
    """G0: exact argument shape. Unknown keys (e.g. `approved: true`) are a block, not ignored."""
    errs: list[str] = []
    spec = CATALOGUE.get(kind)
    if spec is None:
        return {}, [f"G0 unknown action kind {kind!r}"]
    if not isinstance(args, dict):
        return {}, ["G0 args must be an object"]
    allowed = {**spec["args"], **spec["optional"]}
    for k in args:
        if k not in allowed:
            errs.append(f"G0 unexpected argument {k!r} (approvals and overrides are not arguments)")
    out: dict[str, Any] = {}
    for k, typ in allowed.items():
        if k not in args:
            if k in spec["args"]:
                errs.append(f"G0 missing argument {k!r}")
            continue
        v = args[k]
        if typ is int and (isinstance(v, bool) or not isinstance(v, int)):
            errs.append(f"G0 {k} must be an integer")
            continue
        if typ is str and not isinstance(v, str):
            errs.append(f"G0 {k} must be a string")
            continue
        out[k] = v
    dev = out.get("device")
    if dev is not None and not DEVICE_RE.match(dev):
        errs.append(f"G0 device {clean_text(dev, 60)!r} is not a single device name (no wildcards or lists)")
    ifn = out.get("ifname")
    if ifn is not None:
        if unicodedata.normalize("NFKC", ifn) != ifn or not PORT_RE.match(ifn):
            errs.append(f"G0 ifname {clean_text(ifn, 60)!r} is not one physical port "
                        "(ranges, wildcards, units, interface-range names and mgmt ports are refused)")
    if "incident_id" in out and not re.match(r"^[A-Za-z0-9_.:-]{1,64}$", out["incident_id"]):
        errs.append("G0 incident_id has unexpected characters")
    if "topology" in out and not re.match(r"^[a-z0-9-]{1,64}\.toml$", out["topology"]):
        errs.append("G0 topology must be a lab topology file name")
    return out, errs


def effective_role(topology_role: str | None, live_role: str | None) -> str:
    """The more protective of intended (TOML) and live roles. Unknown live role = fabric."""
    t = topology_role or "unused"
    lv = live_role if live_role in ROLE_RANK else "fabric"
    return t if ROLE_RANK.get(t, 4) >= ROLE_RANK[lv] else lv


def evaluate(kind: str, args: dict, reason: str, facts: Facts, policy: dict) -> Decision:
    checked: list[str] = []
    block: list[str] = []
    spec = CATALOGUE.get(kind)
    tier = spec["tier"] if spec else "T2"

    # ---- G0 argument shape, write scope
    checked.append("G0")
    args, errs = validate_args(kind, args)
    block += errs
    if not clean_text(reason):
        block.append("G6 a reason is required")
    dev = args.get("device")
    ifn = args.get("ifname")
    if dev is not None and not errs:
        if dev not in facts.known_devices:
            block.append(f"G0 unknown device {dev!r}")
        if dev in policy.get("protected_devices", []):
            block.append(f"G0 {dev} is protected by policy")
        if ifn is not None:
            if ifn not in policy.get("write_scope_ports", {}).get(dev, []):
                block.append(f"G0 {dev} {ifn} is outside the write scope in lab://policy")
        elif kind == "rollback" and dev not in policy.get("write_scope_devices", []):
            block.append(f"G0 whole-device changes on {dev} are outside the write scope in lab://policy")
    if kind == "rollback" and args.get("rollback_id") not in (None, 1):
        block.append("G4 only `rollback 1` (the newest commit) is in the catalogue")
    if block and errs:
        return Decision(False, tier, "blocked", block, checked)

    # ---- G1 role: access/unused only, never trunk, fabric or routed; most protective source wins
    if kind in ("bounce_port", "disable_port"):
        checked.append("G1")
        if facts.port is None:
            block.append("G1 live port facts unavailable; refusing (fail closed)")
        else:
            live = facts.port.get("live_role")
            role = effective_role(facts.topology_role, live)
            if role not in ("access", "unused"):
                src = []
                if facts.topology_role and facts.topology_role != "unused":
                    src.append(f"topology says {facts.topology_role}")
                if live and live not in ("access", "unused"):
                    src.append(f"live config says {live}")
                block.append(f"G1 {dev} {ifn} is a {role} port ({'; '.join(src) or 'unknown role'}); "
                             "actions only on access ports")
            if facts.topology_role and live and facts.topology_role != live and \
                    {facts.topology_role, live} - {"access", "unused"}:
                block.append(f"G1 role disagreement for {dev} {ifn}: topology={facts.topology_role} "
                             f"live={live}")
            if facts.port.get("interface_range_lines"):
                block.append(f"G1 {dev} {ifn} is an interface-range member; "
                             "a change would act on shared config")
            if facts.port.get("lldp_neighbor"):
                nb = facts.port["lldp_neighbor"].get("remote_system")
                block.append(f"G1 {dev} {ifn} has an LLDP neighbour ({clean_text(nb, 40)}); "
                             "treated as an infrastructure link")
            inh = facts.port.get("config_inherited") or ""
            own = facts.port.get("config_own") or ""
            if inh and inh != own:
                block.append(f"G1 {dev} {ifn} inherits config from apply-groups; "
                             "the effective config is not the port's own")
            if role == "unused" and ifn not in policy.get("test_ports", {}).get(dev, []):
                block.append(f"G1 {dev} {ifn} is unconfigured and not a declared test port")

    # ---- G2 bounce cap
    if kind == "bounce_port":
        checked.append("G2")
        cap = int(policy.get("bounce_cap_24h", 3))
        if facts.bounces_24h >= cap:
            block.append(f"G2 bounce cap reached: {facts.bounces_24h}/{cap} on {dev} {ifn} in 24 h; "
                         "status stays Open and escalates to a human")

    # ---- G3 add VLAN needs two observers (tool not offered in the MVP)
    checked.append("G3")
    if kind == "add_vlan_to_trunk":
        block.append("G3 add_vlan_to_trunk is not enabled")

    # ---- G5 one writer per device
    if dev is not None:
        checked.append("G5")
        if facts.pending_confirm is None:
            block.append(f"G5 could not read {dev}'s commit state; refusing (fail closed)")
        elif facts.pending_confirm:
            block.append(f"G5 {dev} has a pending `commit confirmed`; any commit now would confirm "
                         "someone else's change")
        if facts.lease_holder:
            block.append(f"G5 {dev} is leased by {clean_text(facts.lease_holder, 60)}")
        if facts.open_actions_device:
            block.append(f"G5 {dev} already has an action in flight")
    checked.append("blast")
    if facts.open_actions_total >= int(policy.get("max_open_actions", 3)):
        block.append("blast too many actions in flight across the lab")

    # ---- G6 evidence; no action for client-side faults
    checked.append("G6")
    inc = facts.incident or {}
    has_evidence = bool(args.get("incident_id")) or (ifn in policy.get("test_ports", {}).get(dev or "", []))
    if not has_evidence:
        block.append("G6 no evidence: give an incident_id (or act on a declared test port)")
    if args.get("incident_id") and facts.incident is None:
        block.append(f"G6 incident {args['incident_id']!r} not found")
    kind_of_incident = str(inc.get("kind") or inc.get("classifier") or "").lower()
    if any(k in kind_of_incident for k in policy.get("client_incident_kinds", [])):
        block.append("G6 the incident is client-side; no network action")

    # ---- G7 management plane
    checked.append("G7")
    if dev is not None and not facts.reachable:
        block.append(f"G7 {dev} does not answer NETCONF; never fix a management-plane fault over "
                     "the broken path (wait for the confirm timer or use the console)")
    if "mgmt" in kind_of_incident or "management" in kind_of_incident:
        block.append("G7 management-plane incident: no remote action")

    # ---- G8 drift restored from the generator, never re-learned / rolled back past it
    checked.append("G8")
    if kind == "rollback":
        marker = policy.get("generator_commit_marker", "gen_fabric deploy")
        if facts.newest_commit_log is None:
            block.append("G8 newest commit unknown; refusing")
        elif marker in facts.newest_commit_log:
            block.append("G8 the newest commit is the generator's deploy (source of truth); "
                         "rolling it back would undo intent — use rerun_deploy")
        if not (facts.rollback_diff or "").strip():
            block.append("G4 `rollback 1` would change nothing")

    # ---- G9 services are driver-assist only
    checked.append("G9")
    is_service = any(k in kind_of_incident for k in policy.get("service_incident_kinds", []))

    if block:
        return Decision(False, tier, "blocked", block, checked)

    # ---- G4 approval: T2 always human; T1 self-drives only when toggled and not a service fault
    checked.append("G4")
    if tier == "T1" and policy.get("self_drive", {}).get(kind) and not is_service:
        return Decision(True, tier, "automatic", ["T1 self-driving is on for this action"], checked)
    why = ("T2 actions always need a dashboard approval" if tier == "T2"
           else "G9 service incident: driver-assist only" if is_service
           else "T1 self-driving is off; a dashboard user must approve")
    return Decision(True, tier, "awaiting_approval", [why], checked)
