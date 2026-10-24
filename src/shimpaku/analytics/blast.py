"""Blast radius (R5 §2, recommended rule; code from R5 02_blast_radius.py).

Over a window of SLE units (device-minutes or probe-minutes) with outcome y (bad = 1):
at least 3 failures in scope, one-sided Fisher exact BH q < 0.05, Laplace risk ratio vs the
rest >= 3, coverage >= 0.5; rank by Bayes factor; group scopes that select the identical unit
set and report the most upstream member with its group; otherwise abstain with "diffuse".
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import fisher_exact

from shimpaku.analytics.stats import bh_adjust, log10_bayes_factor_split

# lower = more upstream (topology order for tie-breaks)
SPECIFICITY = {"site": 0, "target": 1, "role": 2, "node": 3, "leaf": 3, "entity_id": 8, "client_id": 8}

RULE = dict(min_support=3, q=0.05, min_rr=3.0, min_cov=0.5)


def candidates(df: pd.DataFrame, y: np.ndarray, features: list[str], min_support: int = 3) -> pd.DataFrame:
    N, F = len(y), int(y.sum())
    rows = []
    for feat in features:
        for v, idx in df.groupby(feat).indices.items():
            n_v = len(idx)
            f_v = int(y[idx].sum())
            if n_v == N:
                continue
            n_o, f_o = N - n_v, F - f_v
            p = fisher_exact([[f_v, n_v - f_v], [f_o, n_o - f_o]], alternative="greater").pvalue
            rows.append(dict(feature=feat, value=v, n=n_v, fails=f_v, rate=f_v / n_v,
                             rr_laplace=((f_v + 1) / (n_v + 2)) / ((f_o + 1) / (n_o + 2)),
                             coverage=(f_v / F) if F else 0.0, p_fisher=float(p),
                             log10_bf=log10_bayes_factor_split(f_v, n_v, f_o, n_o),
                             key=tuple(sorted(idx.tolist())), support_ok=f_v >= min_support))
    c = pd.DataFrame(rows)
    if not c.empty:
        c["q_bh"] = bh_adjust(c.p_fisher.values)
    return c


def blast_radius(units: pd.DataFrame, features: list[str], **rule) -> dict:
    """Return {verdict: scoped|diffuse|no_failures, scope, group, n, fails, table}."""
    r = {**RULE, **rule}
    u = units[units.status.isin(["good", "bad"])].reset_index(drop=True)
    y = (u.status == "bad").to_numpy().astype(int)
    out = dict(rule=r, n=int(len(y)), fails=int(y.sum()))
    if y.sum() == 0:
        return {**out, "verdict": "no_failures", "scope": None, "group": [], "table": []}
    feats = [f for f in features if f in u.columns]
    c = candidates(u.fillna({f: "none" for f in feats}), y, feats, r["min_support"])
    if c.empty:
        return {**out, "verdict": "diffuse", "scope": None, "group": [], "table": []}
    keep = c[c.support_ok & (c.q_bh < r["q"]) & (c.rr_laplace >= r["min_rr"]) & (c.coverage >= r["min_cov"])]
    table = (c.sort_values("log10_bf", ascending=False).drop(columns=["key"]).head(10)
             .round(6).to_dict(orient="records"))
    if keep.empty:
        return {**out, "verdict": "diffuse", "scope": None, "group": [], "table": table}
    keep = keep.sort_values("log10_bf", ascending=False)
    lead = keep.iloc[0]
    eq = keep[keep.key == lead.key]
    eq = eq.assign(spec=eq.feature.map(lambda f: SPECIFICITY.get(f, 5))).sort_values("spec")
    rep = eq.iloc[0]
    return {**out, "verdict": "scoped", "scope": {"feature": rep.feature, "value": str(rep.value),
            "n": int(rep.n), "fails": int(rep.fails), "rr": float(rep.rr_laplace), "coverage": float(rep.coverage),
            "q": float(rep.q_bh), "log10_bf": float(rep.log10_bf)},
            "group": [f"{x.feature}={x.value}" for x in eq.itertuples()], "table": table}
