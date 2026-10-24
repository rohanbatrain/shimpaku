"""Incident open / correlate / close. Pure functions over SLE units; persistence is in db.py.

* **Open** a site incident when either trigger fires on finalized minutes:
  T1 persistence — one entity bad in each of the last :data:`PERSIST_MIN` minutes;
  T2 rate — the multi-window binomial test alarms on the site's bad/n per minute for one SLE.
* **Correlate**: one open incident per site; every bad unit in that site, any SLE, joins it.
  An episode crossing SLEs (FAB-05: overlay BGP down *and* probes unreachable) stays one incident.
* **Primary classifier**: if any application probe-minute is bad, the incident's SLE is
  ``app_reachability`` (user impact first) with its highest-precedence classifier; otherwise
  ``fabric_health``, judged from the service edge — leaf/access-role units when present
  (FAB-01 leaf sees its own link down = Redundancy; FAB-02 leaf sees a neighbour lost with the
  port up = Adjacency), else all units — highest precedence wins.
* **Close** after :data:`CLOSE_QUIET_MIN` finalized minutes with no bad unit in the site;
  ``t_close`` = last bad minute + 1 min.
"""

from __future__ import annotations

import pandas as pd

from shimpaku.analytics import blast, detectors, rules
from shimpaku.analytics.sle import APP, FABRIC, classifier_rank

PERSIST_MIN = 2
PERSIST_RESOURCES_MIN = 5
CLOSE_QUIET_MIN = 5
EDGE_ROLES = ("leaf", "switch", "access")


def _per_minute(u: pd.DataFrame) -> pd.DataFrame:
    c = u[u.status.isin(["good", "bad"])]
    return c.groupby("minute").agg(bad=("status", lambda s: int((s == "bad").sum())), n=("status", "size"))


def triggers(units: pd.DataFrame, site: str, now) -> list[dict]:
    """Return the triggers firing for a site at ``now`` (= end of the last finalized minute).

    Resources (CPU/memory) is a device-health classifier: it opens an incident only after
    :data:`PERSIST_RESOURCES_MIN` minutes and is left out of the rate test, because the lab host
    (load ~19 on 16 threads) and gNMI subscription start make one-minute CPU spikes (seen live
    2026-10-24: cpu.idle 0% for ~30 s on all 7 nodes when the collector subscribed).
    """
    now = pd.Timestamp(now)
    u = units[units.site == site]
    fired = []
    bad = u[u.status == "bad"]
    for eid, g in bad.groupby("entity_id"):
        res_only = set(g.classifier) == {"Resources"}
        k = PERSIST_RESOURCES_MIN if res_only else PERSIST_MIN
        need = {now - pd.Timedelta(minutes=j) for j in range(1, k + 1)}
        mins = set(g.minute) if res_only else set(g[g.classifier != "Resources"].minute)
        if need <= mins:
            fired.append(dict(trigger="persistence", entity=eid, minutes=k))
    for sle in (FABRIC, APP):
        us = u[u.sle == sle]
        us = us.assign(status=us.status.where(us.classifier.fillna("") != "Resources", "good"))
        pm = _per_minute(us)
        if pm.empty:
            continue
        r = detectors.binomial_multiwindow(pm, now - pd.Timedelta(minutes=1))
        spread = [w for w in r["windows"] if w["alarm"]
                  and (pm[(pm.index > now - pd.Timedelta(minutes=1 + w["window_min"]))]["bad"] > 0).sum() >= 2]
        if spread:
            fired.append(dict(trigger="binomial", sle=sle, p=r["score"], p_hat=r["p_hat"]))
    return fired


def primary(bad: pd.DataFrame) -> dict:
    app = bad[bad.sle == APP]
    if not app.empty:
        pool, sle = app, APP
    else:
        fab = bad[bad.sle == FABRIC]
        edge = fab[fab.role.isin(EDGE_ROLES)]
        pool, sle = (edge if not edge.empty else fab), FABRIC
    pool = pool.assign(rank=pool.classifier.map(lambda c: classifier_rank(sle, c)))
    top = pool.sort_values(["rank", "minute"]).iloc[0]
    return dict(sle=sle, classifier=top.classifier, sub_classifier=top.sub_classifier, entity=top.entity_id)


def build(site: str, units: pd.DataFrame, t_open, t_detect, trig: list[dict], incident_id=None) -> dict:
    u = units[(units.site == site) & (units.minute >= t_open)]
    bad = u[u.status == "bad"]
    p = primary(bad)
    unit_dicts = bad.to_dict(orient="records")
    diags = rules.diagnose(unit_dicts)
    classifiers = (bad.groupby(["sle", "classifier", "sub_classifier"]).size().rename("units").reset_index()
                   .to_dict(orient="records"))
    sle_units = u[u.sle == p["sle"]]
    feats = ["node", "role", "target"] if p["sle"] == APP else ["node", "role"]
    br = blast.blast_radius(sle_units, feats)
    br["affected"] = sorted(bad.entity_id.unique().tolist())
    lead = diags[0] if diags else dict(fault_class="unknown", entity=p["entity"], rule=None,
                                       rule_tree=rules.RULE_TREE_VERSION)
    root_causes = [{**lead, "sle": p["sle"], "classifier": p["classifier"], "sub_classifier": p["sub_classifier"],
                    "score": 1.0}] + [{**d, "score": round(1.0 / (i + 2), 3)} for i, d in enumerate(diags[1:])]
    t_open = pd.Timestamp(t_open)
    iid = incident_id or f"INC-{t_open:%Y%m%d-%H%M}-{site}"
    n = len(sle_units[sle_units.status.isin(['good', 'bad'])])
    expl = (f"{p['sle']} · {p['classifier']} / {p['sub_classifier']} at {p['entity']}; "
            f"{len(bad)} bad units since {t_open:%H:%M} (n = {n} {p['sle']} units in window); "
            f"rule {lead.get('rule')} → {lead['fault_class']} ({rules.RULE_TREE_VERSION}); "
            f"blast radius: {br['verdict']}" + (f" {br['scope']['feature']}={br['scope']['value']}" if br.get('scope') else ""))
    return dict(incident_id=iid, site=site, t_open=t_open, t_detect=pd.Timestamp(t_detect), t_close=None,
                sle=p["sle"], root_causes=root_causes,
                blast_radius={**br, "classifiers": classifiers, "triggers": trig}, explanation=expl, status="open")


def step(units: pd.DataFrame, open_incs: list[dict], now, wall_clock=None, last_close=None) -> list[dict]:
    """One correlation step. ``now`` = end of the last finalized minute. Returns incidents to upsert.

    ``last_close`` maps site -> t_close of its latest closed incident: units before it belong to
    that incident and can neither re-trigger nor extend a new one.
    """
    last_close = last_close or {}
    now = pd.Timestamp(now)
    wall = pd.Timestamp(wall_clock) if wall_clock is not None else now
    out = []
    by_site = {i.get("site") or i["incident_id"].rsplit("-", 1)[-1]: i for i in open_incs}
    sites = set(units.site.dropna().unique()) | set(by_site)
    for site in sorted(sites):
        u = units[units.site == site]
        bad = u[u.status == "bad"]
        inc = by_site.get(site)
        if inc is not None:
            recent = bad[bad.minute >= now - pd.Timedelta(minutes=CLOSE_QUIET_MIN)]
            since = bad[bad.minute >= inc["t_open"]]
            if recent.empty:
                last_bad = since.minute.max() if not since.empty else inc["t_open"]
                closed = (build(site, units, inc["t_open"], inc["t_detect"], inc["blast_radius"].get("triggers", []),
                                inc["incident_id"]) if not since.empty else {**inc, "explanation": "", "site": site})
                closed.update(status="closed", t_close=pd.Timestamp(last_bad) + pd.Timedelta(minutes=1))
                out.append(closed)
            else:
                out.append(build(site, units, inc["t_open"], inc["t_detect"],
                                 inc["blast_radius"].get("triggers", []), inc["incident_id"]))
            continue
        lc = last_close.get(site)
        if lc is not None:
            u = u[u.minute >= lc]
            bad = bad[bad.minute >= lc]
        if bad.empty or bad.minute.max() < now - pd.Timedelta(minutes=1):
            continue  # a new incident needs a bad unit in the last finalized minute
        trig = triggers(u, site, now)
        if not trig:
            continue
        # episode start: walk back over contiguous bad minutes (gaps of <= 2 min allowed)
        mins = sorted(set(bad.minute), reverse=True)
        t_open = mins[0]
        for m in mins[1:]:
            if t_open - m <= pd.Timedelta(minutes=3):
                t_open = m
            else:
                break
        out.append(build(site, units, t_open, wall, trig))
    return out
