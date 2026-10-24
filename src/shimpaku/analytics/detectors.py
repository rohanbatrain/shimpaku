"""Detectors (R5 §3 "Ship").

* Counts with a denominator (bad SLE units out of n units per minute): multi-window binomial
  test, windows 5 / 15 / 60 min, alarm when P(X >= k | n, p_hat) < 1e-4. p_hat is the baseline
  rate from history older than the longest window, with a Beta(1, 99) prior (mean 1%) so a cold
  start neither alarms on one bad unit nor is blind.
* Gauges without a denominator (CPU, memory, probe RTT): season-MAD (hour-of-day median and
  1.4826*MAD), |z| > 3.5; upgraded to Holt-Winters additive (5-min bins, m = 288) once >= 3 days
  of history exist, scale from the forecast errors on the held-out last day.

Neither threshold is tuned on test labels; both are fixed a priori as in R5.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from shimpaku.analytics.stats import mad_scale

BINOM_P = 1e-4
WINDOWS_MIN = (5, 15, 60)
PRIOR_A, PRIOR_B = 1.0, 99.0
Z = 3.5
BPD = 288  # 5-minute bins per day


def binomial_multiwindow(per_minute: pd.DataFrame, now, windows=WINDOWS_MIN, p_alarm=BINOM_P) -> dict:
    """``per_minute``: index minute, columns ``bad``, ``n``. Evaluate the windows ending at ``now``."""
    now = pd.Timestamp(now)
    pm = per_minute.sort_index()
    base = pm[pm.index < now - pd.Timedelta(minutes=max(windows))]
    p_hat = (base["bad"].sum() + PRIOR_A) / (base["n"].sum() + PRIOR_A + PRIOR_B)
    res = []
    for w in windows:
        seg = pm[(pm.index > now - pd.Timedelta(minutes=w)) & (pm.index <= now)]
        k, n = int(seg["bad"].sum()), int(seg["n"].sum())
        pv = float(stats.binom.sf(k - 1, n, p_hat)) if n and k else 1.0
        res.append(dict(window_min=w, bad=k, n=n, p=pv, alarm=pv < p_alarm))
    return dict(p_hat=float(p_hat), baseline_n=int(base["n"].sum()), windows=res,
                alarm=any(r["alarm"] for r in res), score=min(r["p"] for r in res))


def _season_mad(hist: pd.Series, t, y) -> tuple[float, float]:
    h = hist.groupby(hist.index.hour)
    hr = pd.Timestamp(t).hour
    if hr in h.groups and len(h.get_group(hr)) >= 6:
        g = h.get_group(hr)
    else:
        g = hist
    med = float(np.median(g))
    scl = max(mad_scale(g), 0.5, 0.02 * abs(med))
    return med, (y - med) / scl


def _holt_winters(hist5: pd.Series):
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    def fit(h):
        return ExponentialSmoothing(h.astype(float).to_numpy(), trend=None, seasonal="add", seasonal_periods=BPD,
                                    initialization_method="heuristic").fit()

    m = fit(hist5)
    bt = fit(hist5.iloc[:-BPD])
    resid = hist5.iloc[-BPD:].to_numpy() - bt.forecast(BPD)
    return float(m.forecast(1)[0]), max(mad_scale(resid), 1e-6)


def gauge_check(series: pd.Series, now) -> dict | None:
    """``series``: values indexed by timestamp (any cadence). Score the last 5-minute bin."""
    if series.empty:
        return None
    s5 = series.sort_index().resample("5min").mean().interpolate(limit=3).dropna()
    now = pd.Timestamp(now).floor("5min")
    if len(s5) < 13:
        return None
    hist, y = s5.iloc[:-1], float(s5.iloc[-1])
    if len(hist) >= 3 * BPD:
        exp, scl = _holt_winters(hist.iloc[-14 * BPD:])
        z, method = (y - exp) / scl, "holt_winters"
    else:
        exp, z = _season_mad(hist, s5.index[-1], y)
        method = "season_mad"
    return dict(method=method, t=s5.index[-1], expected=exp, observed=y, score=float(z), alarm=abs(z) > Z)
