"""View layer: real service payloads -> the UI contract the dashboard pages read.

The dashboard (``web/src/pages``) reads one shape per route; ``api/demo.py`` emits that shape
directly. The real service (``shimpaku.service`` + ``shimpaku.providers``) returns its own shapes,
so ``api/app.py`` passes every read through the matching function here.

Rules (the project's honesty contract):

- Never invent a number. Every value comes from the payload or an obvious computation on it
  (a percentage, a count, a duration between two timestamps, a timestamp parsed from a string).
- What the real system does not collect is ``None`` / ``[]`` and the page says "not collected".
- Statuses are copied, never relabelled (``recurred`` stays ``recurred``).
- Idempotent: a payload already in the UI shape (demo), an ``error`` payload or an
  ``available: false`` payload is returned unchanged.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

try:
    from shimpaku.mcp.guardrails_doc import GUARDRAILS as _GUARDRAIL_DOC
except Exception:  # pragma: no cover - the doc table is optional for the view
    _GUARDRAIL_DOC = []

_GUARDRAIL_NAMES = {g["id"]: g["rule"] for g in _GUARDRAIL_DOC}
_GUARDRAIL_NAMES.setdefault("blast", "blast radius check")

TERMINAL = {"verified", "recurred", "blocked", "failed", "rejected", "cancelled", "rolled_back",
            "fixed_by_operator", "automatic", "validated"}


# ---- helpers -------------------------------------------------------------------
def _skip(real: Any, ui_key: str) -> bool:
    """True when the payload must pass through unchanged."""
    if not isinstance(real, dict):
        return True
    if real.get("error") not in (None, False) or real.get("available") is False:
        return True
    return ui_key in real


def _carry(real: dict, out: dict) -> dict:
    for k in ("replay", "demo"):
        if k in real:
            out[k] = real[k]
    return out


def ts(v: Any) -> float | None:
    """Epoch seconds from a number or an ISO / 'YYYY-MM-DD HH:MM:SS' string (naive = UTC)."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def _human(s: str) -> str:
    s = s.replace("_", " ").replace("-", " ").strip()
    return s[:1].upper() + s[1:]


# ---- service levels -------------------------------------------------------------
def _sle_name(sle: str, site: str | None) -> str:
    # fabric_health is device health (CPU, adjacency, links) on every site's devices; on the campus
    # the word "fabric" would mislabel r1/r2/sw1
    if sle == "fabric_health" and site and site != "fabric":
        return "Device health"
    return _human(sle)


def sle_summary(real: Any) -> Any:
    if _skip(real, "sles") or not isinstance(real.get("sle"), dict):
        return real
    sles = []
    for key, s in real["sle"].items():
        cls = []
        for c in s.get("classifiers") or []:
            name = c.get("classifier") or "?"
            if c.get("sub_classifier"):
                name = f"{name} · {c['sub_classifier']}"
            cls.append({"id": f"{c.get('classifier')}/{c.get('sub_classifier')}", "name": name,
                        "share_pct": c.get("share_pct"), "units": c.get("units")})
        sles.append({
            "id": s.get("sle", key), "name": _sle_name(s.get("sle", key), real.get("site")), "domain": None,
            "goal_pct": None,          # no SLE goal is configured in the real service
            "value_pct": s.get("pct"), "n": s.get("n"), "good": s.get("good"), "bad": s.get("bad"),
            "unit": "units", "label": s.get("label"),
            "series": ((real.get("series") or {}).get(key) or []),  # [] when the provider has none
            "classifiers": cls,
            "incident_id": None,       # the summary does not link an incident
        })
    return _carry(real, {"site": real.get("site"), "window_min": real.get("window_min"), "sites": None,
                         "sles": sles, "changes": real.get("changes")})


# ---- incidents -----------------------------------------------------------------
def _top_cause(i: dict) -> dict | None:
    rc = [r for r in (i.get("root_causes") or []) if isinstance(r, dict)]
    return max(rc, key=lambda r: r.get("score") or 0) if rc else None


def _incident_row(i: dict) -> dict:
    if "id" in i and "opened_at" in i:
        return i
    top = _top_cause(i)
    iid = i.get("incident_id")
    m = re.match(r"^INC-\d{8}-\d{4}-(.+)$", iid or "")  # analytics/incidents.py names INC-<t_open>-<site>
    acts = i.get("actions") or []
    action_id = acts[0].get("id") if acts and isinstance(acts[0], dict) else (acts[0] if acts else None)
    return {
        "id": iid,
        "title": f"{top['fault_class']} · {top['entity']}" if top else (i.get("sle") or iid),
        "site": i.get("site") or (m.group(1) if m else None),
        "sle": i.get("sle"),
        "classifier": " · ".join(x for x in ((top or {}).get("classifier"), (top or {}).get("sub_classifier")) if x) or None,
        "status": i.get("status"),
        "opened_at": ts(i.get("t_open")),
        "detected_at": ts(i.get("t_detect")),
        "closed_at": ts(i.get("t_close")),
        "impact": {"root causes": len(i.get("root_causes") or [])} if i.get("root_causes") is not None else {},
        "action_id": action_id,
        "summary": top.get("why") if top else None,
        "root_causes": i.get("root_causes") or [],
    }


def incident_list(real: Any) -> Any:
    if not isinstance(real, dict) or _skip(real, "__never__") or not isinstance(real.get("incidents"), list):
        return real
    return _carry(real, {"incidents": [_incident_row(i) for i in real["incidents"]]})


def incident(real: Any) -> Any:
    if _skip(real, "opened_at") or "incident_id" not in real:
        return real
    return _carry(real, _incident_row(real))


def blast_radius(real: Any) -> Any:
    if _skip(real, "tree") or not isinstance(real.get("blast_radius"), dict):
        return real
    br = real["blast_radius"]
    # classifier tree: units per classifier / sub-classifier as a share of all bad units
    groups: dict[str, dict[str, int]] = {}
    for c in br.get("classifiers") or []:
        g = groups.setdefault(f"{c.get('sle')} · {c.get('classifier')}", {})
        g[c.get("sub_classifier") or "—"] = g.get(c.get("sub_classifier") or "—", 0) + (c.get("units") or 0)
    total = sum(sum(g.values()) for g in groups.values())
    pct = lambda u: round(100 * u / total, 1) if total else 0.0  # noqa: E731
    tree = [{"name": name, "share_pct": pct(sum(g.values())),
             "children": [{"name": s, "share_pct": pct(u), "children": []} for s, u in g.items()]}
            for name, g in sorted(groups.items(), key=lambda kv: -sum(kv[1].values()))]
    dist: dict[str, list] = {}
    for r in br.get("table") or []:
        dist.setdefault(r.get("feature") or "?", []).append(
            {"name": str(r.get("value")), "failures": r.get("fails"), "total": r.get("n"),
             "rr_laplace": r.get("rr_laplace"), "q_bh": r.get("q_bh")})
    affected = [{"kind": "entity", "id": str(a), "name": str(a), "detail": None, "impact_pct": None,
                 "failure_pct": None, "minutes": None} for a in br.get("affected") or []]
    evidence = [{"kind": r.get("layer") or "root cause", "title": f"{r.get('fault_class')} · {r.get('entity')}",
                 "detail": r.get("why") or "", "tool": f"{r.get('rule')} · score {r.get('score')}",
                 "score": r.get("score"), "minutes": r.get("minutes")}
                for r in sorted(real.get("root_causes") or [], key=lambda r: -(r.get("score") or 0))]
    return _carry(real, {
        "incident_id": real.get("incident_id"), "tree": tree, "timeline": [],  # per-minute fail series: not returned
        "affected": affected, "distribution": [{"dimension": k, "items": v} for k, v in dist.items()],
        "evidence": evidence, "scores": None,
        "verdict": br.get("verdict"), "scope": br.get("scope"), "n": br.get("n"), "fails": br.get("fails"),
        "triggers": br.get("triggers") or [], "rule": br.get("rule"),
    })


# ---- actions -------------------------------------------------------------------
def _title(a: dict) -> str:
    args = a.get("args") or {}
    tail = " ".join(f"{k} {v}" for k, v in args.items() if k != "device")
    return f"{_human(a.get('kind') or 'action')} · {args.get('device', a.get('device', ''))} {tail}".strip()


def _approved_by(a: dict) -> str | None:
    for t in a.get("timeline") or []:
        m = re.match(r"^approved by (\S+)", t.get("note") or "")
        if m and t.get("to") == "approved":
            return m.group(1)
    return None


def _action_row(a: dict) -> dict:
    caller = a.get("caller")
    return {
        "id": a.get("id"), "title": _title(a), "kind": a.get("kind"), "tier": a.get("tier"),
        "device": a.get("device"), "ifname": a.get("ifname"), "site": a.get("site"),
        "status": a.get("status"), "mode": a.get("mode"),
        "proposed_by": caller.get("name") if isinstance(caller, dict) else caller,
        "approved_by": _approved_by(a), "created_at": ts(a.get("created")), "updated_at": ts(a.get("updated")),
        "incident_id": a.get("incident_id"), "error": a.get("error"),
    }


def action_list(real: Any) -> Any:
    if _skip(real, "__never__") or not isinstance(real.get("actions"), list):
        return real
    return _carry(real, {"actions": [a if "created_at" in a else _action_row(a) for a in real["actions"]]})


def _first(tl: list, to: str) -> float | None:
    return next((t.get("ts") for t in tl if t.get("to") == to and t.get("from") != to), None)


def _steps(a: dict) -> list[dict]:
    tl, commits, status = a.get("timeline") or [], a.get("commits") or [], a.get("status")
    steps = [{"state": "proposed", "t": ts(a.get("created"))}]
    if status == "blocked" or (status in ("rejected", "cancelled") and not commits):
        return steps + [{"state": status, "t": ts(_first(tl, status))}]
    steps += [{"state": "approved", "t": _first(tl, "approved")},
              {"state": "committed", "t": commits[0].get("ts") if commits else None},
              {"state": "validating", "t": _first(tl, "validating")}]
    end = status if status in TERMINAL else "verified"
    steps.append({"state": end, "t": _first(tl, end) if status in TERMINAL else None})
    return steps


def _level(t: dict) -> str:
    to, note = t.get("to"), (t.get("note") or "").lower()
    if to in ("verified", "validated"):
        return "good"
    if to in ("recurred", "failed", "blocked", "rolled_back", "rejected"):
        return "bad"
    if "check ok" in note:
        return "good"
    if "fail" in note or "not ok" in note:
        return "bad"
    return "info"


def action(real: Any) -> Any:
    if _skip(real, "steps") or "timeline" not in real:
        return real
    out = _action_row(real)
    pol = real.get("policy") or {}
    reasons = pol.get("reasons") or []
    guard = []
    for g in pol.get("checked") or []:
        fail = next((r for r in reasons if r.startswith(f"{g} ")), None)
        guard.append({"id": g, "name": _GUARDRAIL_NAMES.get(g, g), "passed": fail is None, "detail": fail})
    commits = real.get("commits") or []
    val = real.get("validation") or {}
    checks = val.get("checks") or []
    starts, ends = ts(val.get("starts")), ts(val.get("ends"))
    out.update({
        "args": real.get("args") or {}, "reason": real.get("reason") or "",
        "confirm_minutes": next((c.get("confirm_min") for c in commits if c.get("confirm_min")), None),
        "validation_minutes": round((ends - starts) / 60) if starts and ends else None,
        "committed_at": commits[0].get("ts") if commits else None,
        "steps": _steps(real), "guardrails": guard,
        "policy": {"allowed": pol.get("allowed"), "status": pol.get("status"), "reasons": reasons},
        "diff": None,                 # the executor does not record a config diff
        "commits": commits,
        "validation": [{"t": t.get("ts"), "level": _level(t), "text": t.get("note") or f"{t.get('from')} → {t.get('to')}"}
                       for t in real.get("timeline") or []],
        "series": [{"t": c.get("ts"), "value": 100 if c.get("ok") else 0} for c in checks] or None,
        "series_kind": "validation_checks" if checks else None,
        "verifier": None,             # no independent verifier result in the real payload
    })
    return _carry(real, out)


# ---- inventory / topology / audit ------------------------------------------------
def inventory(real: Any) -> Any:
    if _skip(real, "devices") or not isinstance(real.get("nodes"), list):
        return real
    devs = [{"name": n.get("name"), "site": n.get("site"), "role": n.get("kind"), "model": n.get("kind"),
             "topology": n.get("topology"), "version": None, "mgmt_ip": None, "status": None,
             "uptime_s": None, "cpu_pct": None, "mem_pct": None} for n in real["nodes"]]
    return _carry(real, {"devices": devs, "sites": real.get("sites")})


def topology(real: Any) -> Any:
    if _skip(real, "edges") or not ("lldp" in real or "intended" in real):
        return real
    lldp, intended = real.get("lldp") or [], real.get("intended") or []
    nodes = sorted({x.get("device") for x in intended + lldp} | {x.get("peer") for x in intended}
                   | {x.get("remote_system") for x in lldp} - {None})
    edges = [{"a": x.get("device"), "a_if": x.get("local_if"), "b": x.get("remote_system"),
              "b_if": x.get("remote_port"), "state": None, "source": "lldp"} for x in lldp]
    return _carry(real, {"nodes": [n for n in nodes if n], "edges": edges, "lldp": lldp, "intended": intended})


def audit(real: Any) -> Any:
    if _skip(real, "entries") or not isinstance(real.get("rows"), list):
        return real
    ents = [{"t": ts(r.get("ts")), "principal": (r.get("actor") or {}).get("name") if isinstance(r.get("actor"), dict) else r.get("actor"),
             "tool": r.get("tool"), "args": r.get("args") or {}, "result": r.get("status"),
             "seq": r.get("seq"), "action_id": r.get("action_id"), "row_hash": r.get("row_hash")}
            for r in real["rows"]]
    return _carry(real, {"entries": ents, "rows": real["rows"]})


# ---- bench ---------------------------------------------------------------------
def bench(real: Any) -> Any:
    if _skip(real, "published") or not ("selftest" in real or "runs" in real):
        return real
    st = real.get("selftest") or {}
    scen = []
    for sid, s in sorted((st.get("scenarios") or {}).items()):
        scen.append({"id": sid, "title": s.get("title"), "status": s.get("status"), "trials": s.get("trials"),
                     "passes": s.get("passes"), "fails": s.get("fails"), "blocked": s.get("blocked"),
                     "time_to_effect_s_median": s.get("time_to_effect_s_median"),
                     "time_to_restore_s_median": s.get("time_to_restore_s_median"),
                     "time_to_restore_s_max": s.get("time_to_restore_s_max"),
                     "config_restored_all": s.get("config_restored_all"), "last": ts(s.get("last"))})
    tot = lambda k: sum(x.get(k) or 0 for x in scen)  # noqa: E731
    latest = real.get("latest_run")
    systems = latest.get("systems") if isinstance(latest, dict) and isinstance(latest.get("systems"), list) else None
    out = {
        "published": bool(systems), "runs": real.get("runs") or [], "latest_run": latest,
        "catalogue_sha256": real.get("catalogue_sha256"), "generated_at": ts(real.get("generated")),
        "selftest": {"generated_at": ts(st.get("generated")), "files": st.get("files") or [], "scenarios": scen,
                     "totals": {"scenarios": len(scen), "trials": tot("trials"), "passes": tot("passes"),
                                "fails": tot("fails"), "blocked": tot("blocked"),
                                "config_restored_all": all(x.get("config_restored_all") for x in scen) if scen else None}}
        if st else None,
    }
    if systems:
        out.update({k: latest.get(k) for k in ("run_id", "frozen_tag", "trials", "null_trials", "classes", "per_class", "notes")})
        out["systems"] = systems
    return _carry(real, out)
