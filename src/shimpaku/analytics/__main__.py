"""Read-only replay: ``python -m shimpaku.analytics [--db PATH] [--minutes 60]``.

Opens the DuckDB file read-only (safe beside a running shimpakud), recomputes fabric-health
device-minutes and probe-minutes for the window, replays incident correlation minute by minute
in memory, and prints SLE % with n, the classifier tree, bad minutes per device and incidents.
Nothing is written.
"""

from __future__ import annotations

import argparse
import json

import duckdb
import pandas as pd

from shimpaku.analytics import db, incidents
from shimpaku.analytics.loop import LOOKBACK_H, utcnow
from shimpaku.analytics.sle import APP, FABRIC, fabric_minutes, probe_minutes, summarise


def replay(con, minutes: int, end=None) -> dict:
    cur = con.cursor()
    end = pd.Timestamp(end).floor("min") if end is not None else utcnow().floor("min")
    start = end - pd.Timedelta(minutes=minutes)
    samples = db.metric_minutes(cur, start - pd.Timedelta(hours=LOOKBACK_H), end)
    fab = fabric_minutes(samples, start, end, db.entity_attrs(cur))
    prb = probe_minutes(db.probe_results(cur, start, end), start, end)
    u = pd.concat([fab.assign(client_id=fab.entity_id, target="none", node=fab.entity_id),
                   prb.assign(entity_id=prb.client_id, role="probe")], ignore_index=True)
    opened: dict[str, dict] = {}
    for m in pd.date_range(start + pd.Timedelta(minutes=2), end, freq="min"):
        uu = u[u.minute < m]
        open_list = [i for i in opened.values() if i["status"] == "open"]
        closes = {i["site"]: i["t_close"] for i in opened.values() if i["status"] == "closed"}
        for inc in incidents.step(uu, open_list, m, m, closes):
            inc.setdefault("site", inc["incident_id"].rsplit("-", 1)[-1])
            opened[inc["incident_id"]] = inc
    per_dev = (fab[fab.status == "bad"].groupby(["entity_id", "classifier", "sub_classifier"]).size()
               .rename("minutes").reset_index().to_dict(orient="records"))
    return dict(window=f"{start} .. {end} UTC", metric_rows=len(samples),
                sle={s: summarise(x, s).as_dict() for s, x in ((FABRIC, fab), (APP, prb))},
                na_device_minutes=int((fab.status == "na").sum()) if not fab.empty else 0,
                bad_by_device=per_dev,
                incidents=[dict(id=i["incident_id"], status=i["status"], sle=i["sle"], t_open=str(i["t_open"]),
                                t_detect=str(i["t_detect"]), t_close=str(i.get("t_close")),
                                primary=i["root_causes"][0] if i["root_causes"] else None,
                                blast=i["blast_radius"].get("verdict")) for i in opened.values()])


def main() -> None:
    from shimpaku.config import settings

    ap = argparse.ArgumentParser(prog="python -m shimpaku.analytics")
    ap.add_argument("--db", default=str(settings.duckdb_path))
    ap.add_argument("--minutes", type=int, default=60)
    ap.add_argument("--end", default=None, help="UTC end of window (default now)")
    a = ap.parse_args()
    con = duckdb.connect(a.db, read_only=True)
    print(json.dumps(replay(con, a.minutes, a.end), indent=2, default=str))


if __name__ == "__main__":
    main()
