"""Publish finished results for the dashboard: results.json (the `bench_results()` contract), scores.md
and scoreboard.svg in VIRGA_PUBLISH. Nothing here is written while a run is in progress.

results.json (schema virga/results/v1):
  {"schema", "generated", "catalogue_sha256",
   "selftest": {"generated", "files": [...], "scenarios": {ID: {"title", "status", "trials", "passes",
                "blocked", "fails", "time_to_effect_s_median", "time_to_restore_s_median",
                "time_to_restore_s_max", "config_restored_all", "last"}}} | null,
   "latest_run": {"run_id", "split", "seed", "inject_mode", "fault_trials", "null_trials",
                  "systems": [R5 summary row per system], "per_class": {system: [...]},
                  "cluster_ci": {system: {...}}, "table_md", "plot": "scoreboard.svg"} | null,
   "runs": [{"run_id", "split", "generated"}]}
Non-finite numbers are written as null (JSON has no inf/NaN); a median TTD of null means "never".
"""

from __future__ import annotations

import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path

from virga import RESULTS_SCHEMA


def _clean(o):
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if hasattr(o, "item"):
        return _clean(o.item())
    return o


def load(pub: Path) -> dict:
    p = pub / "results.json"
    if p.exists():
        return json.loads(p.read_text())
    return {"schema": RESULTS_SCHEMA, "selftest": None, "latest_run": None, "runs": []}


def write(pub: Path, data: dict) -> Path:
    pub.mkdir(parents=True, exist_ok=True)
    data["schema"] = RESULTS_SCHEMA
    data["generated"] = datetime.now(timezone.utc).isoformat()
    tmp = pub / "results.json.tmp"
    tmp.write_text(json.dumps(_clean(data), indent=1, default=str))
    tmp.replace(pub / "results.json")
    return pub / "results.json"


def selftest_summary(recs: list[dict], cat, files: list[str]) -> dict:
    by: dict[str, list[dict]] = {}
    for r in recs:
        by.setdefault(r["scenario"], []).append(r)
    out = {}
    for sid, rs in sorted(by.items()):
        tte = [r["phases"].get("verify_injected", {}).get("time_to_effect_s") for r in rs]
        ttr = [r["phases"].get("verify_reverted", {}).get("time_to_restore_s") for r in rs]
        tte = [x for x in tte if x is not None]
        ttr = [x for x in ttr if x is not None]
        out[sid] = {"title": cat.scenarios.get(sid, {}).get("title", rs[0].get("title")),
                    "status": cat.scenarios.get(sid, {}).get("status"),
                    "trials": len(rs), "passes": sum(r["verdict"] == "pass" for r in rs),
                    "blocked": sum(r["verdict"].startswith("blocked") for r in rs),
                    "fails": sum(r["verdict"] == "fail" for r in rs),
                    "time_to_effect_s_median": statistics.median(tte) if tte else None,
                    "time_to_restore_s_median": statistics.median(ttr) if ttr else None,
                    "time_to_restore_s_max": max(ttr) if ttr else None,
                    "config_restored_all": all(r["phases"].get("verify_reverted", {}).get("config_restored") for r in rs),
                    "last": max(r.get("t_end", "") for r in rs)}
    return {"generated": datetime.now(timezone.utc).isoformat(), "files": files, "scenarios": out}


# ---------------------------------------------------------------- scoreboard.svg (no matplotlib on the host)
COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#8e5bd0"]


def scoreboard_svg(summaries: list[dict], per: list, path: Path, tmax: float = 900.0, note: str = "") -> Path:
    W, H, L, T, PW, PH = 980, 380, 60, 40, 520, 270
    bx = L + PW + 120
    bw = W - bx - 40
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="Inter, sans-serif" font-size="11">',
         f'<rect width="{W}" height="{H}" fill="#fcfcfb"/>',
         f'<text x="{L}" y="22" font-size="13" fill="#0b0b0b">Time to detect: higher and further left is better</text>',
         f'<text x="{bx}" y="22" font-size="13" fill="#0b0b0b">Root cause, top-1 (95% Wilson CI)</text>']
    for i in range(6):
        y = T + PH - i * PH / 5
        s.append(f'<line x1="{L}" x2="{L + PW}" y1="{y:.1f}" y2="{y:.1f}" stroke="#e4e3df"/>'
                 f'<text x="{L - 6}" y="{y + 4:.1f}" text-anchor="end" fill="#555">{i * 20}%</text>')
    for i in range(7):
        x = L + i * PW / 6
        s.append(f'<text x="{x:.1f}" y="{T + PH + 16}" text-anchor="middle" fill="#555">{int(i * tmax / 6)}</text>')
    s.append(f'<text x="{L + PW / 2}" y="{T + PH + 34}" text-anchor="middle" fill="#333">seconds after the fault took effect (verified)</text>')
    for k, (summ, P) in enumerate(zip(summaries, per)):
        c = COLORS[k % len(COLORS)]
        n = len(P)
        ttd = sorted(float(x) for x in (P.ttd_s if n else []) if math.isfinite(float(x)))
        pts, y = [(0.0, 0.0)], 0.0
        for j, v in enumerate(ttd, 1):
            v = min(v, tmax)
            pts.append((v, y))
            y = j / n
            pts.append((v, y))
        pts.append((tmax, y))
        d = " ".join(f"{L + px / tmax * PW:.1f},{T + PH - py * PH:.1f}" for px, py in pts)
        s.append(f'<polyline points="{d}" fill="none" stroke="{c}" stroke-width="2"/>')
        s.append(f'<text x="{L + PW + 6}" y="{T + PH - y * PH + 4 - k * 2:.1f}" fill="{c}">{summ["system"]} {100 * y:.0f}% (n={n})</text>')
        yy = T + 20 + k * 36
        lo, hi, p = summ["rca_top1_lo"], summ["rca_top1_hi"], summ["rca_top1"]
        s.append(f'<text x="{bx}" y="{yy - 8}" fill="#333">{summ["system"]}</text>'
                 f'<line x1="{bx + lo * bw:.1f}" x2="{bx + hi * bw:.1f}" y1="{yy}" y2="{yy}" stroke="{c}" stroke-width="2"/>'
                 f'<circle cx="{bx + p * bw:.1f}" cy="{yy}" r="5" fill="{c}"/>'
                 f'<text x="{bx + hi * bw + 6:.1f}" y="{yy + 4}" fill="#333">{100 * p:.0f}%</text>')
    s.append(f'<line x1="{bx}" x2="{bx + bw}" y1="{T + PH}" y2="{T + PH}" stroke="#999"/>'
             f'<text x="{bx}" y="{T + PH + 16}" fill="#555">0%</text>'
             f'<text x="{bx + bw}" y="{T + PH + 16}" text-anchor="end" fill="#555">100%</text>')
    if note:
        s.append(f'<text x="{L}" y="{H - 8}" fill="#e34948">{note}</text>')
    s.append("</svg>")
    path.write_text("\n".join(s))
    return path
