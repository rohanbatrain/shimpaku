"""Demo payloads for the operator ops panels (``/api/ops/*``) when ``SHIMPAKU_DEMO=1``.

Shapes match ``shimpaku.hostmon.snapshot`` and ``shimpaku.gateway.GatewayClient.get``. The host
scenario replays the 2026-10-25 overheat: the package climbs into the 90s over the last hours.
"""

from __future__ import annotations

import math
import random
import time

from shimpaku.hostmon import GUARD_C, GUARD_HOLD_S, WARN_C

_VMS = [("r1", "running"), ("r2", "running"), ("sw1", "running"), ("spine1", "running"),
        ("spine2", "running"), ("leaf1", "running"), ("leaf2", "shut off")]


def host() -> dict:
    now = time.time()
    rnd = random.Random(7)
    series, thr = [], 0
    for i in range(288):  # 24 h at 5 min
        t = now - (287 - i) * 300
        hot = max(0.0, (i - 200) / 88)  # the last ~7 h heat up
        temp = 71 + 6 * math.sin(i / 20) + 22 * hot + rnd.uniform(-1.5, 1.5)
        d = int(max(0, temp - 92) * rnd.uniform(150, 400))
        thr += d
        series.append({"t": round(t), "temp_c": round(min(temp, 98.4), 1), "load1": round(9 + 5 * hot + rnd.uniform(-1, 1), 2),
                       "mem_pct": round(78 + 6 * hot + rnd.uniform(-1, 1), 1), "throttle": d})
    last = series[-1]
    latest = {"t": now, "temp_c": last["temp_c"], "temp_source": "coretemp", "temp_crit_c": 100.0,
              "throttle": {"core": thr * 3, "package": thr}, "fans": [],
              "ncpu": 16, "load": [last["load1"], 12.8, 11.1], "mem_total_mb": 31_808, "mem_avail_mb": 5_120,
              "vms_running": 6}
    return {"demo": True, "sampler": True, "latest": latest, "series": series,
            "vms": {"vms": [{"id": str(i + 1) if s == "running" else None, "name": n, "state": s}
                            for i, (n, s) in enumerate(_VMS)], "error": None, "t": now},
            "warn_c": WARN_C,
            "guard": {"enabled": True, "threshold_c": GUARD_C, "hold_s": GUARD_HOLD_S,
                      "events": [{"t": now - 1260, "kind": "thermal_guard", "temp_c": 97.6, "threshold_c": GUARD_C, "held_s": 60}]}}


def gateway() -> dict:
    now = time.time()
    models = [
        {"name": "nvidia-nemotron-ultra", "requests": 412, "errors": 9, "spend_usd": 1.8421, "tokens": 1_204_311, "p50_ms": 9800, "p95_ms": 21400},
        {"name": "nvidia-nemotron-lightning", "requests": 958, "errors": 4, "spend_usd": 0.6120, "tokens": 1_850_022, "p50_ms": 3100, "p95_ms": 9900},
        {"name": "glm-flash", "requests": 37, "errors": 31, "spend_usd": 0.0, "tokens": 12_400, "p50_ms": 118000, "p95_ms": 120000},
    ]
    keys = [
        {"name": "shimpaku-daemon", "requests": 1180, "errors": 12, "spend_usd": 2.1033, "tokens": 2_700_000, "p50_ms": 5200, "p95_ms": 18800},
        {"name": "shimpaku-lease-L7", "requests": 190, "errors": 1, "spend_usd": 0.3108, "tokens": 330_000, "p50_ms": 4100, "p95_ms": 12000},
        {"name": "…9f3a", "requests": 37, "errors": 31, "spend_usd": 0.04, "tokens": 36_733, "p50_ms": 118000, "p95_ms": 120000},
    ]
    for g in models + keys:
        g["error_pct"] = round(100 * g["errors"] / g["requests"], 1)
    tot = {k: sum(m[k] for m in models) for k in ("requests", "errors", "tokens")}
    tot.update(spend_usd=round(sum(m["spend_usd"] for m in models), 4), p50_ms=4200, p95_ms=19800,
               error_pct=round(100 * tot["errors"] / tot["requests"], 1))
    return {"demo": True, "connected": True, "cached": False, "fetched_at": now, "window_h": 24, "errors": [],
            "health": {"alive": True, "ready": True, "db": "connected", "version": "1.79.3"},
            "totals": tot, "models": models, "keys": keys,
            "sampled": {"rows": 1000, "total": 1407, "capped": True},
            "models_health": [{"model": "nvidia-nemotron-ultra", "healthy": True},
                              {"model": "nvidia-nemotron-lightning", "healthy": True},
                              {"model": "glm-flash", "healthy": False}]}
