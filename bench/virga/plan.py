"""Seeded, randomised schedules (R5 §6.4, REVIEW B4).

Blocks: every class once per block in seeded random order, null (control) trials mixed in so that at
least `control_p` of all trials are null. Gap after each trial = hold + uniform(gap) + 1-59 s jitter,
so starts drift off minute boundaries and times of day spread. Intensities are drawn from each
scenario's range. The plan is written before execution and is reproducible from (seed, catalogue).

Splits: `dev` excludes held-out scenarios; `test` includes them and requires the catalogue to match
the frozen hash (`virga freeze`), so thresholds cannot be tuned against test seeds.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timezone

from virga import PLAN_SCHEMA


def null_per_block(n_faults: int, control_p: float) -> int:
    """Smallest m with m / (n_faults + m) >= control_p (at least one)."""
    if control_p <= 0:
        return 0
    return max(1, math.ceil(control_p * n_faults / (1 - control_p) - 1e-9))


def make_plan(cat, ids: list[str], seed: int, count: int, gap=(600, 1200), control_p: float = 0.2,
              split: str = "dev", inject_mode: str = "confirmed", null_hold_s: int = 300) -> dict:
    rng = random.Random(seed)
    pool = sorted(ids)
    items, t, block = [], rng.randint(1, 59), 0
    while sum(1 for i in items if i["kind"] == "fault") < count:
        block += 1
        order = pool[:]
        rng.shuffle(order)
        for _ in range(null_per_block(len(order), control_p)):
            order.insert(rng.randrange(len(order) + 1), None)
        for sid in order:
            if sid is not None and sum(1 for i in items if i["kind"] == "fault") >= count:
                continue
            if sid is None:
                hold = null_hold_s
                items.append({"n": len(items) + 1, "t_offset_s": t, "block": block, "kind": "control",
                              "scenario": "CTRL-00", "hold_s": hold})
            else:
                sc = cat.scenarios[sid]
                inten = {k: (rng.randint(*v["range"]) if "range" in v else v["default"])
                         for k, v in ((sc.get("toggle") or {}).get("intensity") or {}).items()}
                hold = (sc.get("timing") or {}).get("detect_budget_s",
                                                    cat.lab["timing_defaults"]["detect_budget_s"])
                items.append({"n": len(items) + 1, "t_offset_s": t, "block": block, "kind": "fault", "scenario": sid,
                              "intensity": inten, "hold_s": hold})
            t += hold + round(rng.uniform(*gap)) + rng.randint(1, 59)
    n_null = sum(1 for i in items if i["kind"] == "control")
    return {"schema": PLAN_SCHEMA, "seed": seed, "split": split, "created": datetime.now(timezone.utc).isoformat(),
            "catalogue_sha256": cat.sha256, "inject_mode": inject_mode, "concurrency": "one-at-a-time",
            "gap_s": list(gap), "control_p": control_p, "null_share": round(n_null / max(1, len(items)), 3),
            "scenarios": pool, "holdout_included": sorted(set(pool) & cat.holdout_ids), "items": items}
