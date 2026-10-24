"""Catalogue schema (virga/lab/v1 + virga/scenario/v1) and its validator.

The shape is R9 §2.1 (Chaos Toolkit-like: `probes` with `healthy` = steady-state hypothesis, `inject`
= method, `revert` = rollbacks) plus R5 §6.1 ground-truth fields. `validate()` returns a list of
human-readable errors; an empty list means the catalogue is usable. It never touches the lab.
"""

from __future__ import annotations

import re
import string

LAYERS = {"fabric-underlay", "fabric-overlay", "campus-l2", "campus-l3", "mgmt", "services", "wifi", "wan",
          "config", "control"}
STATUSES = {"designed", "executable", "selftested", "blocked"}
PROBE_KINDS = {"ospf", "bfd", "nexthops", "bgp", "ping", "iface", "iface_flapped_ago", "intent_missing",
               "ping_counter_delta", "tcp", "host_ping", "pending_commit", "top_commit", "clock_offset", "sh"}
STEP_KINDS = {"junos", "junos_op", "sh", "wait", "close_sessions", "auto", "flap", "note"}
EXPECT_OPS = {"eq", "ne", "lt", "le", "gt", "ge", "in", "not_in", "match"}
TIMING_KEYS = {"preflight_timeout_s", "inject_timeout_s", "revert_timeout_s", "poll_s", "fast_poll_s", "stable",
               "inject_ttl_min", "detect_budget_s", "cooldown_s"}
ENTITY_RE = re.compile(r"^(none|(node|intf|link|client|service|ap|ssid|vlan):\S+)$")
ID_RE = re.compile(r"^[A-Z]{2,5}-\d{2}$")
REQUIRED = ("id", "title", "layer", "status", "executable", "requires", "inject", "revert", "ground_truth")
SCENARIO_KEYS = set(REQUIRED) | {"kind", "toggle", "health", "health_override", "probes", "compound",
                                 "expect_inject_rejected", "timing", "expect", "remediation", "risk", "cleanup",
                                 "verify", "bench", "holdout"}


def _vars_in(obj) -> set[str]:
    out: set[str] = set()
    if isinstance(obj, str):
        for m in string.Template.pattern.finditer(obj):
            name = m.group("named") or m.group("braced")
            if name:
                out.add(name)
    elif isinstance(obj, list):
        for x in obj:
            out |= _vars_in(x)
    elif isinstance(obj, dict):
        for v in obj.values():
            out |= _vars_in(v)
    return out


def _check_expect(spec, where: str, errs: list[str]) -> None:
    if isinstance(spec, dict):
        bad = set(spec) - EXPECT_OPS
        if bad:
            errs.append(f"{where}: unknown expectation operator(s) {sorted(bad)}")


def _check_probe(p, where: str, errs: list[str]) -> None:
    if not isinstance(p, dict):
        errs.append(f"{where}: probe must be a mapping")
        return
    for k in ("id", "kind"):
        if k not in p:
            errs.append(f"{where}: probe missing {k!r}")
    if p.get("kind") not in PROBE_KINDS:
        errs.append(f"{where}: probe {p.get('id')!r} unknown kind {p.get('kind')!r}")
    if not isinstance(p.get("args", {}), dict):
        errs.append(f"{where}: probe {p.get('id')!r} args must be a mapping")
    for phase in ("healthy", "faulted"):
        if phase in p:
            _check_expect(p[phase], f"{where}: probe {p.get('id')!r} {phase}", errs)


def _step_nodes(steps) -> list[str]:
    out = []
    for st in steps or []:
        if isinstance(st, dict):
            for k in ("junos", "junos_op", "auto", "flap"):
                if isinstance(st.get(k), dict) and st[k].get("node"):
                    out.append(st[k]["node"])
    return out


def validate_lab(lab: dict) -> list[str]:
    errs: list[str] = []
    if lab.get("kind") != "lab":
        errs.append("first document must be `kind: lab`")
    nodes = lab.get("nodes")
    if not isinstance(nodes, dict) or not nodes:
        errs.append("lab: `nodes` must be a non-empty mapping")
        nodes = {}
    for n, spec in nodes.items():
        if not isinstance(spec, dict) or "mgmt" not in spec:
            errs.append(f"lab: node {n} needs `mgmt`")
    write = (lab.get("allocation") or {}).get("write", [])
    for n in write:
        if n not in nodes:
            errs.append(f"lab: allocation.write names unknown node {n}")
    td = lab.get("timing_defaults") or {}
    missing = TIMING_KEYS - set(td)
    if missing:
        errs.append(f"lab: timing_defaults missing {sorted(missing)}")
    for name, probes in (lab.get("health_sets") or {}).items():
        for i, p in enumerate(probes or []):
            _check_probe(p, f"lab: health_sets.{name}[{i}]", errs)
            if "healthy" not in p:
                errs.append(f"lab: health_sets.{name} probe {p.get('id')} has no `healthy` expectation")
    for i, k in enumerate((lab.get("decoys") or {}).get("kinds", [])):
        if not k.get("apply") or not k.get("revert") or not k.get("nodes"):
            errs.append(f"lab: decoys.kinds[{i}] needs nodes, apply and revert")
        for n in k.get("nodes", []):
            if n not in write:
                errs.append(f"lab: decoys.kinds[{i}] node {n} is outside allocation.write")
    return errs


def validate_scenario(sc: dict, lab: dict) -> list[str]:
    sid = sc.get("id", "?")
    errs: list[str] = []
    w = f"{sid}"
    for k in REQUIRED:
        if k not in sc:
            errs.append(f"{w}: missing required key {k!r}")
    if errs:
        return errs
    extra = set(sc) - SCENARIO_KEYS
    if extra:
        errs.append(f"{w}: unknown key(s) {sorted(extra)}")
    if not ID_RE.match(str(sid)):
        errs.append(f"{w}: id must look like FAB-01")
    if sc["layer"] not in LAYERS:
        errs.append(f"{w}: layer {sc['layer']!r} not in {sorted(LAYERS)}")
    if sc["status"] not in STATUSES:
        errs.append(f"{w}: status {sc['status']!r} not in {sorted(STATUSES)}")
    if not isinstance(sc["executable"], bool):
        errs.append(f"{w}: executable must be true/false")
    elif sc["executable"] and sc["status"] not in ("executable", "selftested"):
        errs.append(f"{w}: executable: true needs status executable or selftested (has {sc['status']})")
    elif not sc["executable"] and sc["status"] in ("executable", "selftested"):
        errs.append(f"{w}: status {sc['status']} but executable: false")
    if not isinstance(sc["requires"], list):
        errs.append(f"{w}: requires must be a list")

    gt = sc["ground_truth"]
    for k in ("fault_class", "target_entity", "root_cause", "blast_radius"):
        if k not in gt:
            errs.append(f"{w}: ground_truth missing {k!r}")
    if "target_entity" in gt and not ENTITY_RE.match(str(gt["target_entity"])):
        errs.append(f"{w}: ground_truth.target_entity {gt['target_entity']!r} is not a canonical R5 id")
    for a in gt.get("entity_aliases", []) or []:
        if not ENTITY_RE.match(str(a)):
            errs.append(f"{w}: entity alias {a!r} is not a canonical R5 id")

    nodes = lab.get("nodes", {})
    write = set((lab.get("allocation") or {}).get("write", []))
    for phase in ("inject", "revert"):
        steps = sc[phase]
        if not isinstance(steps, list):
            errs.append(f"{w}: {phase} must be a list of steps")
            continue
        for i, st in enumerate(steps):
            keys = set(st) if isinstance(st, dict) else set()
            if len(keys) != 1 or not keys <= STEP_KINDS:
                errs.append(f"{w}: {phase}[{i}] must be exactly one of {sorted(STEP_KINDS)}")
                continue
            if "note" in keys and sc["executable"]:
                errs.append(f"{w}: {phase}[{i}] is a `note` but the scenario is executable")
            if "junos" in keys and not st["junos"].get("lines"):
                errs.append(f"{w}: {phase}[{i}] junos step has no lines")
            if "auto" in keys and phase != "revert":
                errs.append(f"{w}: `auto` is a revert-only step")
        for n in _step_nodes(steps):
            if n not in nodes:
                errs.append(f"{w}: {phase} names unknown node {n}")
            elif sc["executable"] and n not in write:
                errs.append(f"{w}: {phase} writes {n}, outside allocation.write {sorted(write)}")
    if sc["executable"] and not sc.get("expect_inject_rejected") and any("junos" in s or "flap" in s for s in sc["inject"]) \
            and not sc["revert"]:
        errs.append(f"{w}: executable Junos inject without a revert")
    if any("auto" in s for s in sc["revert"]):
        ttl = [s["junos"].get("ttl_min") for s in sc["inject"] if "junos" in s]
        if not ttl or any(t is None or t > 2 for t in ttl):
            errs.append(f"{w}: an `auto` revert needs every inject commit to carry ttl_min <= 2")

    intens = ((sc.get("toggle") or {}).get("intensity") or {})
    for name, spec in intens.items():
        if "default" not in spec:
            errs.append(f"{w}: intensity {name} has no default")
        rng = spec.get("range")
        if rng and not (len(rng) == 2 and rng[0] <= spec.get("default", rng[0]) <= rng[1]):
            errs.append(f"{w}: intensity {name} default outside range {rng}")
    if sc["executable"]:
        known = set(lab.get("vars", {})) | set(intens)
        unresolved = (_vars_in(sc["inject"]) | _vars_in(sc["revert"]) | _vars_in(sc.get("probes", []))) - known
        if unresolved:
            errs.append(f"{w}: unresolved variable(s) {sorted(unresolved)}")

    sets = lab.get("health_sets") or {}
    ids = set()
    for hs in sc.get("health", []) or []:
        if hs not in sets:
            errs.append(f"{w}: health set {hs!r} not in the lab header")
        else:
            ids |= {f"{hs}:{p['id']}" for p in sets[hs]}
    for i, p in enumerate(sc.get("probes", []) or []):
        _check_probe(p, f"{w}: probes[{i}]", errs)
        ids.add(p.get("id"))
    for o in sc.get("health_override", []) or []:
        if o.get("id") not in ids:
            errs.append(f"{w}: health_override {o.get('id')!r} matches no probe")
        for phase in ("healthy", "faulted"):
            if phase in o:
                _check_expect(o[phase], f"{w}: health_override {o.get('id')} {phase}", errs)
    if sc["executable"] and not sc.get("expect_inject_rejected"):
        judged = [p for p in sc.get("probes", []) or [] if "faulted" in p] + \
                 [o for o in sc.get("health_override", []) or [] if "faulted" in o]
        if not judged:
            errs.append(f"{w}: executable scenario has no faulted expectation (the fault could never be proven)")
    unknown_t = set(sc.get("timing", {}) or {}) - TIMING_KEYS
    if unknown_t:
        errs.append(f"{w}: unknown timing key(s) {sorted(unknown_t)}")
    rem = sc.get("remediation")
    if rem is not None and not isinstance(rem.get("accept", []), list):
        errs.append(f"{w}: remediation.accept must be a list")
    return errs


def validate(lab: dict, scenarios: list[dict]) -> list[str]:
    errs = validate_lab(lab)
    seen: dict[str, int] = {}
    for sc in scenarios:
        seen[sc.get("id")] = seen.get(sc.get("id"), 0) + 1
        errs += validate_scenario(sc, lab)
    errs += [f"duplicate scenario id {i}" for i, n in seen.items() if n > 1]
    for sc in scenarios:
        for m in sc.get("compound", []) or []:
            if m not in seen:
                errs.append(f"{sc.get('id')}: compound member {m} unknown")
    return errs
