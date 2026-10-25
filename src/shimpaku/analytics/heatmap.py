"""Probe heatmap: stored RPM results (``probe_result``) as a path × time grid (``/api/probes/heatmap``).

One row per probe path (the RPM test a device runs: ``rpm:<node>:<owner>/<test>``), one column per
time bucket. Every cell carries the raw aggregates and its ``n`` (results in the bucket), plus how
many of its probe-minutes the app-reachability SLE scored bad (``bad_minutes`` of ``minutes``),
using the same rule as the SLE (``sle.probe_minutes``) so a red stripe here is the same fault the
Service levels page counts.

Guest safety: a path is named by its device and test name only. The target address is never
returned (it is a lab address), and nothing here reads config or credentials.
"""

from __future__ import annotations

import math

import pandas as pd

from shimpaku.analytics import vocab as V
from shimpaku.analytics.sle import probe_minutes, site_role

COLS = ["ts", "client_id", "target", "kind", "ok", "rtt_ms", "jitter_ms", "loss_pct"]


def bucket_for(window_min: int) -> int:
    """Minutes per column: one minute up to 2 h; 15 min for a day; 2 h for a week (≤ ~100 columns)."""
    if window_min <= 120:
        return 1
    if window_min <= 1440:
        return 15
    return 120


def _num(x) -> float | None:
    if x is None:
        return None
    x = float(x)
    return None if math.isnan(x) else round(x, 3)


def path_label(client_id: str) -> tuple[str, str]:
    """``rpm:sw1:shimpaku/to-leaf1`` -> (``sw1``, ``to-leaf1``)."""
    node = V.node_of(client_id)
    test = client_id.split(":", 2)[-1] if client_id.count(":") >= 2 else client_id
    return node, test.split("/", 1)[-1]


def build(probes: pd.DataFrame, start, end, site: str, window_min: int) -> dict:
    """``probes``: ``probe_result`` rows (any subset of :data:`COLS`); ``start``/``end`` naive UTC."""
    step = bucket_for(window_min)
    start = pd.Timestamp(start).floor(f"{step}min")
    end = pd.Timestamp(end)
    cols = list(pd.date_range(start, end, freq=f"{step}min", inclusive="left"))
    epoch = [c.tz_localize("UTC").timestamp() for c in cols]
    base = {"site": site, "window_min": window_min, "bucket_min": step, "columns": epoch,
            "thresholds": {"loss_pct": V.APP_LOSS_PCT, "rtt_ms": V.APP_RTT_MS},
            "threshold_version": V.THRESHOLD_VERSION,
            "source": "Junos RPM results stored by the collector (probe_result); faults = SLE-bad probe-minutes"}
    if probes is None or probes.empty:
        return {**base, "paths": []}
    p = probes.copy()
    for c in COLS:
        if c not in p.columns:
            p[c] = None
    p["ts"] = pd.to_datetime(p["ts"])
    p = p[(p.ts >= start) & (p.ts < end)]
    p["node"] = p["client_id"].map(V.node_of)
    p = p[p["node"].map(lambda n: site_role(n)[0]) == site]
    if p.empty:
        return {**base, "paths": []}
    p["bucket"] = p["ts"].dt.floor(f"{step}min")
    # loss as the SLE reads it: a result with no loss figure counts as total loss
    p["loss"] = pd.to_numeric(p["loss_pct"], errors="coerce").fillna(100.0)
    for c in ("rtt_ms", "jitter_ms"):
        p[c] = pd.to_numeric(p[c], errors="coerce")
    pm = probe_minutes(p[["ts", "client_id", "target", "kind", "ok", "rtt_ms", "loss_pct"]], start, end)
    if not pm.empty:
        pm["bucket"] = pd.to_datetime(pm["minute"]).dt.floor(f"{step}min")
    index = {c: i for i, c in enumerate(cols)}
    paths = []
    for cid, g in sorted(p.groupby("client_id"), key=lambda kv: kv[0]):
        node, test = path_label(cid)
        cells: list[dict | None] = [None] * len(cols)
        mins = pm[pm.client_id == cid] if not pm.empty else pm
        for b, gb in g.groupby("bucket"):
            i = index.get(b)
            if i is None:
                continue
            mb = mins[mins.bucket == b] if not mins.empty else mins
            bad = mb[mb.status == "bad"] if not mb.empty else mb
            cls = bad["classifier"].mode().iloc[0] if not bad.empty else None
            cells[i] = {"n": int(len(gb)), "loss_pct": _num(gb["loss"].mean()), "rtt_ms": _num(gb["rtt_ms"].mean()),
                        "jitter_ms": _num(gb["jitter_ms"].mean()), "minutes": int(len(mb)),
                        "bad_minutes": int(len(bad)), "classifier": cls}
        paths.append({"id": cid, "node": node, "test": test, "label": f"{node} → {test}",
                      "n": int(len(g)), "cells": cells})
    return {**base, "paths": paths}
