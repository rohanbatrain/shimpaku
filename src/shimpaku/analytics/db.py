"""Thin data access for analytics over the DuckDB store (R5 07_schema.sql; CONTRACT data model).

Analytics reads ``entity``, ``metric_sample``, ``probe_result`` and writes ``device_minute``,
``probe_minute``, ``anomaly``, ``incident``. It accepts whatever the daemon hands it: a store
object exposing a DuckDB connection (``.duckdb``, ``.conn``, ``.con`` or ``.connection()``), or a
DuckDB connection itself. Each call uses its own ``cursor()`` so it is safe from a worker thread.
"""

from __future__ import annotations

import json

import duckdb
import pandas as pd

from shimpaku.analytics import vocab as V

# analytics-owned tables, created only if the store has not created them (columns = R5 schema)
DDL = [
    """CREATE TABLE IF NOT EXISTS device_minute (minute TIMESTAMP, entity_id VARCHAR, sle VARCHAR, status VARCHAR,
       classifier VARCHAR, sub_classifier VARCHAR, evidence JSON)""",
    """CREATE TABLE IF NOT EXISTS probe_minute (minute TIMESTAMP, client_id VARCHAR, sle VARCHAR, status VARCHAR,
       classifier VARCHAR, sub_classifier VARCHAR, value DOUBLE, threshold_version VARCHAR, leaf VARCHAR,
       ap VARCHAR, ssid VARCHAR, vlan VARCHAR, access VARCHAR, evidence JSON)""",
    """CREATE TABLE IF NOT EXISTS anomaly (anomaly_id VARCHAR, series VARCHAR, method VARCHAR, t_start TIMESTAMP,
       t_end TIMESTAMP, score DOUBLE, expected DOUBLE, observed DOUBLE)""",
    """CREATE TABLE IF NOT EXISTS incident (incident_id VARCHAR, t_open TIMESTAMP, t_detect TIMESTAMP,
       t_close TIMESTAMP, sle VARCHAR, root_causes JSON, blast_radius JSON, explanation VARCHAR, status VARCHAR)""",
]


def lock_of(store):
    """The store's lock (store-api.md: the daemon shares one Store that serialises calls)."""
    import contextlib
    return getattr(store, "_lock", None) or contextlib.nullcontext()


def connection(store) -> duckdb.DuckDBPyConnection:
    if isinstance(store, duckdb.DuckDBPyConnection):
        return store
    for attr in ("duckdb", "conn", "con", "db"):
        c = getattr(store, attr, None)
        if callable(c) and not isinstance(c, duckdb.DuckDBPyConnection):
            c = c()
        if isinstance(c, duckdb.DuckDBPyConnection):
            return c
    if callable(getattr(store, "connection", None)):
        return store.connection()
    raise TypeError(f"cannot get a DuckDB connection from {type(store).__name__}")


def ensure_tables(cur) -> None:
    for d in DDL:
        cur.execute(d)


def tables(cur) -> set[str]:
    return {r[0] for r in cur.execute("SELECT table_name FROM information_schema.tables").fetchall()}


def metric_minutes(cur, since, until) -> pd.DataFrame:
    """One row per (minute, entity, engine metric): last value; CPU = 100 - min(idle); memory = max."""
    names = list(V.METRIC_ALIASES)
    df = cur.execute(
        """SELECT date_trunc('minute', ts) AS ts, entity_id, metric,
                  arg_max(value, ts) AS v_last, min(value) AS v_min, max(value) AS v_max
           FROM metric_sample WHERE ts >= ? AND ts < ? AND metric IN (SELECT unnest(?))
           GROUP BY 1, 2, 3""", [since, until, names]).df()
    if df.empty:
        return pd.DataFrame(columns=["ts", "entity_id", "metric", "value"])
    agg = df["metric"].map(lambda m: V.MINUTE_AGG.get(m, "last"))
    df["value"] = df["v_last"].where(agg == "last", df["v_min"].where(agg == "min", df["v_max"]))
    for m, fn in V.METRIC_TRANSFORM.items():
        sel = df.metric == m
        df.loc[sel, "value"] = df.loc[sel, "value"].map(fn)
    df["metric"] = df["metric"].map(V.METRIC_ALIASES)
    return df[["ts", "entity_id", "metric", "value"]]


def probe_results(cur, since, until) -> pd.DataFrame:
    if "probe_result" not in tables(cur):
        return pd.DataFrame(columns=["ts", "client_id", "target", "kind", "ok", "rtt_ms", "loss_pct"])
    return cur.execute("SELECT ts, client_id, target, kind, ok, rtt_ms, loss_pct FROM probe_result "
                       "WHERE ts >= ? AND ts < ?", [since, until]).df()


def entity_attrs(cur) -> dict[str, dict]:
    if "entity" not in tables(cur):
        return {}
    out = {}
    for eid, attrs in cur.execute("SELECT entity_id, attrs FROM entity").fetchall():
        try:
            out[eid] = json.loads(attrs) if isinstance(attrs, str) else (attrs or {})
        except ValueError:
            out[eid] = {}
    return out


def _n(x):
    """NaN/NaT -> NULL (pandas turns None into NaN in mixed columns)."""
    return None if x is None or (not isinstance(x, (str, dict, list)) and pd.isna(x)) else x


def _j(x):
    return json.dumps(x, default=str)


def replace_device_minutes(cur, df: pd.DataFrame, since, until) -> None:
    cur.execute("DELETE FROM device_minute WHERE minute >= ? AND minute < ? AND sle = 'fabric_health'", [since, until])
    if df.empty:
        return
    rows = [(r.minute, r.entity_id, r.sle, r.status, _n(r.classifier), _n(r.sub_classifier),
             _j({**r.evidence, "site": r.site, "role": r.role})) for r in df.itertuples()]
    cur.executemany("INSERT INTO device_minute VALUES (?, ?, ?, ?, ?, ?, ?)", rows)


def replace_probe_minutes(cur, df: pd.DataFrame, since, until) -> None:
    cur.execute("DELETE FROM probe_minute WHERE minute >= ? AND minute < ? AND sle = 'app_reachability'", [since, until])
    if df.empty:
        return
    rows = [(r.minute, r.client_id, r.sle, r.status, _n(r.classifier), _n(r.sub_classifier), _n(r.value), r.threshold_version,
             r.leaf, None, None, None, r.access, _j({**r.evidence, "target": r.target, "site": r.site}))
            for r in df.itertuples()]
    cur.executemany("INSERT INTO probe_minute VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)


def units(cur, since, until) -> pd.DataFrame:
    """Device- and probe-minutes as one frame with site/role/node/target unpacked."""
    dm = cur.execute("SELECT minute, entity_id, sle, status, classifier, sub_classifier, evidence FROM device_minute "
                     "WHERE minute >= ? AND minute < ? AND sle = 'fabric_health'", [since, until]).df()
    pm = cur.execute("SELECT minute, client_id, sle, status, classifier, sub_classifier, evidence FROM probe_minute "
                     "WHERE minute >= ? AND minute < ? AND sle = 'app_reachability'", [since, until]).df()
    frames = []
    for df, key in ((dm, "entity_id"), (pm, "client_id")):
        if df.empty:
            continue
        df = df.copy()
        df["evidence"] = df["evidence"].map(lambda s: json.loads(s) if isinstance(s, str) else (s or {}))
        df["site"] = df["evidence"].map(lambda e: e.get("site"))
        df["role"] = df["evidence"].map(lambda e: e.get("role", "probe"))
        df["target"] = df["evidence"].map(lambda e: e.get("target", "none"))
        df["node"] = df[key].map(V.node_of)
        if key == "client_id":
            df["entity_id"] = df["client_id"]
        else:
            df["client_id"] = df["entity_id"]
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["minute", "entity_id", "client_id", "sle", "status", "classifier",
                                     "sub_classifier", "evidence", "site", "role", "target", "node"])
    return pd.concat(frames, ignore_index=True)


def open_incidents(cur) -> list[dict]:
    rows = cur.execute("SELECT incident_id, t_open, t_detect, sle, root_causes, blast_radius FROM incident "
                       "WHERE status = 'open'").fetchall()
    return [dict(incident_id=r[0], t_open=pd.Timestamp(r[1]), t_detect=pd.Timestamp(r[2]), sle=r[3],
                 root_causes=json.loads(r[4] or "[]"), blast_radius=json.loads(r[5] or "{}")) for r in rows]


def last_closes(cur) -> dict[str, pd.Timestamp]:
    rows = cur.execute("SELECT incident_id, t_close FROM incident WHERE status = 'closed'").fetchall()
    out: dict[str, pd.Timestamp] = {}
    for iid, tc in rows:
        site = iid.rsplit("-", 1)[-1]
        if tc is not None and (site not in out or pd.Timestamp(tc) > out[site]):
            out[site] = pd.Timestamp(tc)
    return out


def upsert_incident(cur, inc: dict) -> None:
    cur.execute("DELETE FROM incident WHERE incident_id = ?", [inc["incident_id"]])
    cur.execute("INSERT INTO incident VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [inc["incident_id"], inc["t_open"], inc["t_detect"], inc.get("t_close"), inc["sle"],
                 _j(inc["root_causes"]), _j(inc["blast_radius"]), inc["explanation"], inc["status"]])


def insert_anomalies(cur, rows: list[dict]) -> None:
    if rows:
        cur.executemany("INSERT INTO anomaly VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        [(a["anomaly_id"], a["series"], a["method"], a["t_start"], a["t_end"], a["score"],
                          a["expected"], a["observed"]) for a in rows])
