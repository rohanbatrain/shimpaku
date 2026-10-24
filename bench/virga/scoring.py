"""R5 §6 scoring contract, integrated (from R5-files/06_scoring.py + r5common.py), plus REVIEW B5.

`records_to_trials` turns Virga run records into R5 trials.jsonl rows: t_effective and t_cleared are
the runner's MEASURED first-OK times; a fault that was never proven is void (excluded, not failed).
`score_system` is R5's matcher and formulas unchanged. `class_stats` adds what REVIEW B5 asks for:
per-class Wilson rows (descriptive only) and cluster-bootstrap CIs that resample fault classes, since
repeats of one class on one link are not independent.
"""

from __future__ import annotations

import math
from datetime import datetime

import numpy as np
import pandas as pd
from scipy import stats

EPS_S = 30            # clock tolerance before t_effective
GRACE_S = 120         # after t_cleared, alerts still belong to the trial (recovery noise)


# ---------------------------------------------------------------- r5common (verbatim)
def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


def garwood(k: int, alpha: float = 0.05) -> tuple[float, float]:
    lo = 0.0 if k == 0 else stats.chi2.ppf(alpha / 2, 2 * k) / 2
    hi = stats.chi2.ppf(1 - alpha / 2, 2 * k + 2) / 2
    return (lo, hi)


# ---------------------------------------------------------------- run records -> R5 trials
def _ts(s) -> float:
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()


def canonical(e: str) -> str:
    """Catalogue blast-radius shorthand -> R5 canonical ids (leaf1 -> node:leaf1, leaf1:ge-0/0/0 -> intf:...)."""
    if ":" not in e:
        return f"node:{e}"
    head = e.split(":", 1)[0]
    if head in ("node", "intf", "link", "client", "service", "ap", "ssid", "vlan"):
        return e
    return f"intf:{e}"


def records_to_trials(recs: list[dict], run_id: str, seed=None, deadline_s: int = 600) -> list[dict]:
    trials = []
    for n, r in enumerate(recs, 1):
        tid = r.get("trial_id") or f"T{n:03d}"
        if r.get("kind") == "control" or r.get("scenario") == "CTRL-00":
            trials.append({"trial_id": tid, "run_id": run_id, "seed": seed, "block": r.get("block"),
                           "scenario": "CTRL-00", "fault_class": "none", "layer": "-", "target_entity": "none",
                           "entity_aliases": {}, "t_inject": _ts(r["t_start"]), "t_effective": _ts(r["t_start"]),
                           "t_cleared": _ts(r["t_end"]), "detect_deadline_s": deadline_s, "impacted_entities": [],
                           "t_fix_verified": None, "collateral": False, "unsafe_actions": 0,
                           "host_load_1m": r.get("host_load_1m"), "leakage_audit": "null trial"})
            continue
        gt = r.get("ground_truth") or {}
        ph = r.get("phases", {})
        vi, vr, ag = ph.get("verify_injected", {}), ph.get("verify_reverted", {}), ph.get("agent", {})
        if not vi.get("ok") or r.get("expect_inject_rejected") or gt.get("fault_class") == "none":
            continue  # void: fault not proven (or a validation check, not a fault)
        t_ref = _ts(ph["inject"]["t_ref"]) if "t_ref" in ph.get("inject", {}) else _ts(r["t_start"])
        target = gt.get("target_entity", "node:?")
        decoys = [d for d in ag.get("decoys", []) if d.get("applied")]
        trials.append({
            "trial_id": tid, "run_id": run_id, "seed": seed, "block": r.get("block"), "scenario": r["scenario"],
            "fault_class": gt.get("fault_class", "unknown"), "layer": r.get("layer"), "target_entity": target,
            "entity_aliases": {target: list(gt.get("entity_aliases") or [])},
            "t_inject": t_ref, "t_effective": t_ref + (vi.get("time_to_effect_s") or 0),
            "t_cleared": vr.get("t_first_ok") or _ts(r["t_end"]), "detect_deadline_s": deadline_s,
            "impacted_entities": sorted({canonical(e) for e in (gt.get("blast_radius") or {}).get("entities", [])}),
            "impact_basis": "designed scope (catalogue); measured client impact needs the farm",
            "verification": {
                "effective_evidence": "; ".join(f"{v['probe']}={v['value']}" for v in vi.get("confirm", {}).get("values", [])),
                "cleared_evidence": f"healthy probes + config_restored={vr.get('config_restored')}"},
            "t_fix_verified": ag.get("t_fix_verified"), "collateral": not vr.get("config_restored", False),
            "unsafe_actions": int(ag.get("unsafe_actions", 0)), "host_load_1m": r.get("host_load_1m"),
            "inject_mode": r.get("inject_mode"), "holdout": r.get("holdout", False),
            "leakage_audit": f"neutral commit comment; {len(decoys)} decoy commit(s) in window; "
                             f"inject {r.get('inject_mode')}"})
    return trials


def run_meta(trials: list[dict], run_id: str, seeds=None, **extra) -> dict:
    start = min(t["t_inject"] for t in trials)
    end = max(t["t_cleared"] for t in trials)
    busy = sum(t["t_cleared"] - t["t_inject"] for t in trials if t["fault_class"] != "none")
    return {"run_id": run_id, "start": start, "end": end, "clean_observation_s": max(1.0, end - start - busy),
            "seeds": seeds or [], **extra}


# ---------------------------------------------------------------- R5 06_scoring.score_system (unchanged logic)
def same_entity(pred: str, truth: str, aliases: dict) -> bool:
    return pred == truth or pred in aliases.get(truth, [])


def score_system(trials, incidents, run, name):
    trials = sorted(trials, key=lambda t: t["t_effective"])
    used = set()
    per = []
    for t in trials:
        if t["fault_class"] == "none":
            continue
        lo, hi = t["t_effective"] - EPS_S, t["t_effective"] + t["detect_deadline_s"]
        cand = [i for i in incidents if lo <= i["t_detect"] <= hi and i["incident_id"] not in used]
        cand.sort(key=lambda i: i["t_detect"])
        r = dict(trial_id=t["trial_id"], fault_class=t["fault_class"], layer=t["layer"], detected=bool(cand))
        if cand:
            inc = cand[0]
            used.add(inc["incident_id"])
            for j in incidents:
                if t["t_effective"] - EPS_S <= j["t_detect"] <= t["t_cleared"] + GRACE_S:
                    used.add(j["incident_id"])
            r["ttd_s"] = max(0.0, inc["t_detect"] - t["t_effective"])
            ranked = inc.get("root_causes", [])
            al = t.get("entity_aliases", {})
            ok_e = [same_entity(c["entity"], t["target_entity"], al) for c in ranked]
            ok_ec = [e and c["fault_class"] == t["fault_class"] for e, c in zip(ok_e, ranked)]
            r["top1"], r["top3"] = any(ok_ec[:1]), any(ok_ec[:3])
            r["top1_entity"] = any(ok_e[:1])
            P, G = set(inc.get("blast_radius", [])), set(t["impacted_entities"])
            if not G:
                r["br_precision"] = r["br_recall"] = 1.0 if not P else 0.0
            else:
                r["br_precision"] = len(P & G) / len(P) if P else 0.0
                r["br_recall"] = len(P & G) / len(G)
            fix = t.get("t_fix_verified")
            r["remediated"] = fix is not None and not t.get("collateral", False)
            r["ttvr_s"] = (fix - t["t_effective"]) if r["remediated"] else math.nan
            r["unsafe_actions"] = int(t.get("unsafe_actions", 0))
        else:
            r.update(ttd_s=math.inf, top1=False, top3=False, top1_entity=False, br_precision=0.0,
                     br_recall=0.0, remediated=False, ttvr_s=math.nan, unsafe_actions=int(t.get("unsafe_actions", 0)))
        per.append(r)
    P = pd.DataFrame(per)
    fp = [i for i in incidents if i["incident_id"] not in used]
    days = run["clean_observation_s"] / 86400
    n, k = len(P), int(P.detected.sum()) if len(P) else 0
    lo, hi = wilson(k, n)
    g_lo, g_hi = garwood(len(fp))
    t1 = int(P.top1.sum()) if n else 0
    t1lo, t1hi = wilson(t1, n)
    det = P[P.detected] if n else P
    f1 = (2 * det.br_precision * det.br_recall / (det.br_precision + det.br_recall).replace(0, np.nan)).fillna(0) \
        if len(det) else pd.Series(dtype=float)
    rem = int(P.remediated.sum()) if n else 0
    rlo, rhi = wilson(rem, n)
    matched = len([i for i in incidents if i["incident_id"] in used])
    summary = dict(system=name, fault_trials=n, incidents=len(incidents),
                   detection=k / n if n else 0.0, detection_lo=lo, detection_hi=hi,
                   fp=len(fp), fp_per_day=len(fp) / days, fp_lo=g_lo / days, fp_hi=g_hi / days,
                   precision=matched / len(incidents) if incidents else math.nan,
                   ttd_median_s=float(np.median(P.ttd_s)) if n else math.inf,
                   ttd_mean_detected_s=float(det.ttd_s.mean()) if len(det) else math.nan,
                   rca_top1=t1 / n if n else 0.0, rca_top1_lo=t1lo, rca_top1_hi=t1hi,
                   rca_top3=float(P.top3.mean()) if n else 0.0,
                   rca_top1_entity=float(P.top1_entity.mean()) if n else 0.0,
                   br_f1=float(f1.mean()) if len(det) else 0.0,
                   remediated=rem / n if n else 0.0, remediated_lo=rlo, remediated_hi=rhi,
                   ttvr_median_s=float(P.ttvr_s.median()) if n else math.nan,
                   unsafe_actions=int(P.unsafe_actions.sum()) if n else 0)
    return summary, P


def fmt_table(S):
    out = ["| System | n | Detected [95% CI] | FP/day [95% CI] | Median TTD | RCA top-1 [95% CI] | RCA top-3 | Blast-radius F1 | Fixed & verified | Median TTVR | Unsafe actions |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in S:
        ttd = "never" if math.isinf(s["ttd_median_s"]) else f"{s['ttd_median_s']:.0f} s"
        ttvr = "—" if math.isnan(s["ttvr_median_s"]) else f"{s['ttvr_median_s'] / 60:.1f} min"
        out.append(f"| {s['system']} | {s['fault_trials']} | {100 * s['detection']:.0f}% [{100 * s['detection_lo']:.0f}–{100 * s['detection_hi']:.0f}] "
                   f"| {s['fp_per_day']:.1f} [{s['fp_lo']:.1f}–{s['fp_hi']:.1f}] | {ttd} "
                   f"| {100 * s['rca_top1']:.0f}% [{100 * s['rca_top1_lo']:.0f}–{100 * s['rca_top1_hi']:.0f}] "
                   f"| {100 * s['rca_top3']:.0f}% | {s['br_f1']:.2f} | {100 * s['remediated']:.0f}% | {ttvr} | {s['unsafe_actions']} |")
    return "\n".join(out)


# ---------------------------------------------------------------- REVIEW B5: statistics by fault class
def class_stats(P: pd.DataFrame, n_boot: int = 2000, seed: int = 0) -> dict:
    """Per-class Wilson rows (descriptive) + cluster bootstrap over classes for pooled detection/top-1."""
    if P is None or not len(P):
        return {"per_class": [], "cluster_ci": {}}
    rows = []
    for fc, g in P.groupby("fault_class"):
        n, k, t1 = len(g), int(g.detected.sum()), int(g.top1.sum())
        dlo, dhi = wilson(k, n)
        tlo, thi = wilson(t1, n)
        rows.append({"fault_class": fc, "n": n, "detected": k / n, "detected_lo": dlo, "detected_hi": dhi,
                     "rca_top1": t1 / n, "rca_top1_lo": tlo, "rca_top1_hi": thi})
    rng = np.random.default_rng(seed)
    groups = [g for _, g in P.groupby("fault_class")]
    det, top = [], []
    for _ in range(n_boot):
        pick = [groups[i] for i in rng.integers(0, len(groups), len(groups))]
        B = pd.concat(pick)
        det.append(B.detected.mean())
        top.append(B.top1.mean())
    q = lambda xs: [float(np.quantile(xs, 0.025)), float(np.quantile(xs, 0.975))]  # noqa: E731
    return {"per_class": rows, "cluster_ci": {"classes": len(groups), "n_boot": n_boot,
                                              "detection": q(det), "rca_top1": q(top)}}
