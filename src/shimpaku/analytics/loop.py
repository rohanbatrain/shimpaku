"""Analytics scheduler: the daemon calls :func:`run_analytics_loop`.

Each pass (every ``every_s``, default 60 s), on finalized minutes only:
1. recompute fabric-health device-minutes and probe-minutes for the last :data:`RECOMPUTE_MIN`
   minutes (late samples are absorbed), with a :data:`LOOKBACK_H` h lookback for "expected";
2. assert classifier sums; 3. open / correlate / close incidents; 4. every 5 min, gauge detectors
   (season-MAD → Holt-Winters) on CPU and memory into ``anomaly``.
"""

from __future__ import annotations

import asyncio
import logging

import pandas as pd

from shimpaku.analytics import db, detectors, incidents
from shimpaku.analytics.sle import APP, FABRIC, assert_classifier_sums, fabric_minutes, probe_minutes, summarise

log = logging.getLogger("shimpaku.analytics")

RECOMPUTE_MIN = 5
LOOKBACK_H = 6
UNIT_WINDOW_H = 24


def utcnow() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC").tz_localize(None)


def run_once(store, now=None) -> dict:
    """One analytics pass; holds the shared Store's lock (store-api.md) for its duration."""
    with db.lock_of(store):
        return _run_once(store, now)


def _run_once(store, now=None) -> dict:
    wall = pd.Timestamp(now) if now is not None else utcnow()
    end = wall.floor("min")  # minutes < end are finalized
    start = end - pd.Timedelta(minutes=RECOMPUTE_MIN)
    cur = db.connection(store).cursor()
    try:
        db.ensure_tables(cur)
        samples = db.metric_minutes(cur, end - pd.Timedelta(hours=LOOKBACK_H), end)
        attrs = db.entity_attrs(cur)
        fab = fabric_minutes(samples, start, end, attrs)
        assert_classifier_sums(fab)
        prb = probe_minutes(db.probe_results(cur, start, end), start, end)
        assert_classifier_sums(prb)
        cur.execute("BEGIN")
        db.replace_device_minutes(cur, fab, start, end)
        db.replace_probe_minutes(cur, prb, start, end)
        units = db.units(cur, end - pd.Timedelta(hours=UNIT_WINDOW_H), end)
        changed = incidents.step(units, db.open_incidents(cur), end, wall, db.last_closes(cur))
        for inc in changed:
            db.upsert_incident(cur, inc)
        anomalies = gauge_pass(samples, prb, end) if end.minute % 5 == 0 else []
        db.insert_anomalies(cur, anomalies)
        cur.execute("COMMIT")
        recent = units[units.minute >= end - pd.Timedelta(hours=1)]
        return dict(now=str(end), device_minutes=len(fab), probe_minutes=len(prb),
                    sle={s: summarise(recent, s).as_dict() for s in (FABRIC, APP)},
                    incidents=[dict(id=i["incident_id"], status=i["status"], sle=i["sle"],
                                    classifier=i["root_causes"][0].get("classifier") if i["root_causes"] else None)
                               for i in changed], anomalies=len(anomalies))
    except Exception:
        try:
            cur.execute("ROLLBACK")
        except Exception:  # noqa: BLE001 - nothing open
            pass
        raise
    finally:
        cur.close()


def gauge_pass(samples: pd.DataFrame, probes: pd.DataFrame, end) -> list[dict]:
    out = []
    g = samples[samples.metric.isin(["cpu_pct", "mem_pct"])]
    for (eid, metric), s in g.groupby(["entity_id", "metric"]):
        r = detectors.gauge_check(s.set_index(pd.to_datetime(s.ts))["value"], end)
        if r and r["alarm"]:
            out.append(dict(anomaly_id=f"A-{eid}-{metric}-{r['t']:%Y%m%d%H%M}", series=f"{eid}/{metric}",
                            method=r["method"], t_start=r["t"], t_end=r["t"] + pd.Timedelta(minutes=5),
                            score=r["score"], expected=r["expected"], observed=r["observed"]))
    return out


async def run_analytics_loop(store, every_s: int = 60) -> None:
    """Run :func:`run_once` forever, a few seconds after each minute boundary."""
    while True:
        try:
            res = await asyncio.to_thread(run_once, store)
            log.info("analytics pass %s: fabric %s, app %s, incidents %s", res["now"],
                     res["sle"][FABRIC]["label"], res["sle"][APP]["label"], res["incidents"])
        except Exception:  # keep the daemon alive; the failure is logged with its traceback
            log.exception("analytics pass failed")
        now = utcnow()
        delay = every_s - (now.second + now.microsecond / 1e6) % every_s + 3 if every_s == 60 else every_s
        await asyncio.sleep(delay)
