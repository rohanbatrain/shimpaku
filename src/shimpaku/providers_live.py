"""Store providers for the live views (``api/routes_live.py``): per-port traffic rates, the
Junos event tail, and as-of-minute state for the time-travel scrubber.

What the collector stores (``collector/__init__.py``, ``store/schema.sql``):

- ``metric_sample``: raw gNMI SAMPLE rows every 10 s (``SAMPLE_INTERVAL_S``) — interface
  counters ``if.in_octets`` / ``if.out_octets`` (and packets, errors), ``if.oper_up``, CPU,
  memory, ``ospf.full`` / ``ospf.state`` per ``<node>:ospf:<ifname.unit>``, ``bfd.up`` per
  ``<node>:bfd:<peer address>``, ``bgp.established``, and ``collector.connected`` per node.
- ``event``: Junos events from ``/junos/events`` (target-defined), filtered by
  ``parse.allowed_event``, ``event_id`` from a sequence.
- ``device_minute`` / ``probe_minute``: the analytics loop's per-minute verdicts (every 60 s).

Nothing purges these tables (no retention job exists), so history reaches back to the first
sample; the scrubber offers at most the last 24 h of it. Link speed is not collected (no
``port-speed`` subscription), so utilisation cannot be computed and is never guessed.

Every provider reuses the daemon's single DuckDB connection (see ``providers.py``).
"""

from __future__ import annotations

import json
import re
from typing import Any

import pandas as pd

from shimpaku import service
from shimpaku.analytics import db as adb

PHYS = re.compile(r"^(ge|xe|et|mge)-\d+/\d+/\d+$")
RATE_WINDOW_S = 90          # two 10 s samples fit easily; older means the port went quiet
STATE_WINDOW_MIN = 10       # an oper / OSPF sample older than this is not "the state as of t"
SPEED_REASON = "link speed is not collected (the collector subscribes to counters and oper-status only)"


def _epoch(t) -> float | None:
    if t is None or (not isinstance(t, str) and pd.isna(t)):
        return None
    t = pd.Timestamp(t)
    return (t.tz_localize("UTC") if t.tzinfo is None else t).timestamp()


def _naive(t: float | None) -> pd.Timestamp:
    """Epoch seconds (None = now) -> naive UTC timestamp as stored."""
    if t is None:
        return pd.Timestamp.now(tz="UTC").tz_localize(None)
    return pd.Timestamp(float(t), unit="s")


def port_rates(cur, at: float | None = None) -> dict:
    """bits/s in and out per physical port from the last two counter samples at or before ``at``."""
    end = _naive(at)
    rows = cur.execute(
        "SELECT entity_id, metric, ts, value FROM ("
        " SELECT entity_id, metric, ts, value,"
        "  row_number() OVER (PARTITION BY entity_id, metric ORDER BY ts DESC) AS rn"
        " FROM metric_sample WHERE ts > ? AND ts <= ? AND metric IN ('if.in_octets', 'if.out_octets'))"
        " WHERE rn <= 2 ORDER BY entity_id, metric, ts",
        [end - pd.Timedelta(seconds=RATE_WINDOW_S), end]).fetchall()
    pairs: dict[tuple[str, str], list] = {}
    for eid, metric, ts, v in rows:
        pairs.setdefault((eid, metric), []).append((_epoch(ts), v))
    ports: dict[str, dict[str, dict]] = {}
    for (eid, metric), pts in pairs.items():
        node, _, ifn = eid.partition(":")
        if not PHYS.match(ifn):
            continue
        p = ports.setdefault(node, {}).setdefault(ifn, {"in_bps": None, "out_bps": None, "t": None})
        key = "in_bps" if metric == "if.in_octets" else "out_bps"
        if len(pts) == 2:
            (t0, v0), (t1, v1) = pts
            if t1 and t0 and t1 > t0 and v1 is not None and v0 is not None and v1 >= v0:  # a reset gives nothing
                p[key] = round((v1 - v0) * 8 / (t1 - t0), 1)
        p["t"] = max(p["t"] or 0, pts[-1][0] or 0) or None
    return {"at": _epoch(end), "window_s": RATE_WINDOW_S, "ports": ports}


def _classify_priority(p: Any) -> str | None:
    if p is None:
        return None
    s = str(p).strip().lower()
    names = {"0": "error", "1": "error", "2": "error", "3": "error", "4": "warning", "5": "info", "6": "info", "7": "info",
             "emergency": "error", "alert": "error", "critical": "error", "error": "error", "warning": "warning",
             "notice": "info", "info": "info", "debug": "info"}
    return names.get(s) or next((v for k, v in names.items() if not k.isdigit() and k in s), None)


KINDS = (("link", ("SNMP_TRAP_LINK_", "RPD_IFD_", "RPD_IFL_", "RPD_IFA_", "KERNEL", "LACPD_", "MIB2D_")),
         ("commit", ("UI_COMMIT", "UI_ROLLBACK", "UI_LOAD_EVENT", "UI_CFG_AUDIT")),
         ("ospf", ("RPD_OSPF_",)), ("bgp", ("RPD_BGP_", "BGP_")), ("bfd", ("BFDD_",)),
         ("lldp", ("LLDP_",)), ("chassis", ("CHASSISD_", "ALARMD_", "FPC_", "PFE_", "LICENSE_")))


def kind_of(etype: str) -> str:
    for k, prefixes in KINDS:
        if etype.startswith(prefixes):
            return k
    return "other"


def severity_of(etype: str, attrs: dict) -> str:
    sev = _classify_priority(attrs.get("priority"))
    if sev:
        return sev
    if re.search(r"(DOWN|FAIL|ERR|ROLLBACK|NOT_CONFIRMED|EXPIRED|VIOLATION)", etype):
        return "warning"
    return "info"


def event_row(r) -> dict:
    eid, ts, ingest, node, entity, etype, attrs, raw = r
    try:
        a = json.loads(attrs) if isinstance(attrs, str) else (attrs or {})
    except ValueError:
        a = {}
    ent = str(entity or "")
    return {"id": int(eid), "t": _epoch(ts), "ingest_t": _epoch(ingest), "device": node,
            "interface": ent.split(":", 1)[1] if ":" in ent else None, "type": etype,
            "kind": kind_of(etype or ""), "severity": severity_of(etype or "", a),
            "message": raw or a.get("message") or "", "attrs": a}


def register_all(store) -> None:
    from shimpaku.providers import off_loop

    def _cur():
        return adb.connection(store).cursor()

    def _run(fn):
        with adb.lock_of(store):
            cur = _cur()
            try:
                if "metric_sample" not in adb.tables(cur):
                    return {"available": False, "reason": "the collector has not created its tables"}
                return fn(cur)
            finally:
                cur.close()

    def live_port_rates(at: float | None = None) -> dict:
        out = _run(lambda cur: port_rates(cur, at))
        if out.get("available") is False:
            return out
        return {**out, "speed_bps": None, "speed_reason": SPEED_REASON,
                "source": "gNMI interface counters (metric_sample, 10 s samples): Δoctets × 8 / Δt"}

    def live_events(after_id: int | None = None, before_id: int | None = None, limit: int = 200,
                          device: str | None = None, q: str | None = None) -> dict:
        limit = max(1, min(int(limit), 1000))

        def fn(cur):
            if "event" not in adb.tables(cur):
                return {"available": False, "reason": "no event table yet"}
            where, args = [], []
            if after_id is not None:
                where.append("event_id > ?"); args.append(int(after_id))
            if before_id is not None:
                where.append("event_id < ?"); args.append(int(before_id))
            if device:
                where.append("node = ?"); args.append(device)
            if q:
                where.append("(raw ILIKE ? OR type ILIKE ?)"); args += [f"%{q}%", f"%{q}%"]
            sql = ("SELECT event_id, ts, ingest_ts, node, entity_id, type, attrs, raw FROM event"
                   + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY event_id DESC LIMIT ?")
            rows = cur.execute(sql, [*args, limit]).fetchall()
            last = cur.execute("SELECT max(event_id), count(*), min(ts) FROM event").fetchone()
            return {"events": [event_row(r) for r in rows], "last_id": last[0], "stored": int(last[1] or 0),
                    "first_t": _epoch(last[2]),
                    "source": "Junos events from gNMI /junos/events, as the collector stores them (event table)"}
        return _run(fn)

    def live_range() -> dict:
        def fn(cur):
            m = cur.execute("SELECT min(ts), max(ts) FROM metric_sample WHERE metric = 'if.in_octets'").fetchone()
            d = cur.execute("SELECT min(minute), max(minute) FROM device_minute").fetchone() \
                if "device_minute" in adb.tables(cur) else (None, None)
            return {"samples": {"first": _epoch(m[0]), "last": _epoch(m[1]), "interval_s": 10},
                    "minutes": {"first": _epoch(d[0]), "last": _epoch(d[1]), "interval_s": 60},
                    "retention": "none: nothing purges metric_sample, event or the minute tables"}
        return _run(fn)

    def live_state_at(t: float) -> dict:
        """Stored state as of minute ``t``: port oper, OSPF adjacency per interface, collector
        reachability, the device-minute verdict, and traffic rates."""
        end = _naive(t)
        start = end - pd.Timedelta(minutes=STATE_WINDOW_MIN)

        def fn(cur):
            rows = cur.execute(
                "SELECT entity_id, metric, arg_max(value, ts), max(ts) FROM metric_sample "
                "WHERE ts > ? AND ts <= ? AND metric IN ('if.oper_up', 'ospf.full', 'collector.connected') "
                "GROUP BY 1, 2", [start, end]).fetchall()
            dm = cur.execute(
                "SELECT entity_id, arg_max(status, minute), arg_max(classifier, minute), arg_max(sub_classifier, minute), "
                "max(minute) FROM device_minute WHERE minute > ? AND minute <= ? AND sle = 'fabric_health' GROUP BY 1",
                [end - pd.Timedelta(minutes=2), end]).fetchall() if "device_minute" in adb.tables(cur) else []
            ports: dict[str, dict] = {}
            ospf: dict[str, dict] = {}
            connected: dict[str, bool] = {}
            for eid, metric, v, _ts in rows:
                node, _, rest = eid.partition(":")
                if metric == "if.oper_up" and PHYS.match(rest):
                    ports.setdefault(node, {})[rest] = "up" if v >= 0.5 else "down"
                elif metric == "ospf.full" and rest.startswith("ospf:"):
                    ospf.setdefault(node, {})[rest[5:].split(".", 1)[0]] = "Full" if v >= 0.5 else "not Full"
                elif metric == "collector.connected" and not rest:
                    connected[node] = v >= 0.5
            verdicts = {r[0]: {"status": r[1], "classifier": r[2], "sub_classifier": r[3], "minute": _epoch(r[4])}
                        for r in dm}
            return {"t": _epoch(end), "ports": ports, "ospf": ospf, "connected": connected, "verdicts": verdicts,
                    "traffic": port_rates(cur, t)}
        return _run(fn)

    def live_sle_at(site: str, t: float, window_min: int = 60) -> dict:
        """``sle_get_summary`` for the hour ending at ``t`` (same units, same summarise)."""
        from shimpaku.analytics.sle import APP, FABRIC, summarise

        end = _naive(t)
        with adb.lock_of(store):
            cur = _cur()
            try:
                units = adb.units(cur, end - pd.Timedelta(minutes=window_min), end)
            finally:
                cur.close()
        if "site" in units.columns:
            units = units[units["site"] == site]
        return {"site": site, "window_min": window_min, "end": _epoch(end),
                "sle": {s: summarise(units, s).as_dict() for s in (FABRIC, APP)}, "series": {}, "changes": []}

    for name, fn in (("live_port_rates", live_port_rates), ("live_events", live_events), ("live_range", live_range),
                     ("live_state_at", live_state_at), ("live_sle_at", live_sle_at)):
        service.register_provider(name, off_loop(fn))  # synchronous bodies: never on the event loop
