"""Service providers: the small glue the orchestrator writes to plug analytics and bench data
into `shimpaku.service` (CONTRACT: `register_provider(name, fn)`).

Every other data path in `service.py` already talks to a builder's module directly
(`shimpaku.devices` for reads, `shimpaku.actions.engine` for actions, and `topo_get_neighbors`
falls back to a live LLDP read when no "topology" provider is registered — so this module does
not register one). These providers reuse the daemon's single DuckDB connection: DuckDB refuses
a second connection to the same file with a different configuration from the same process, so
a provider must never open its own `Store`.
"""

from __future__ import annotations

import json
import logging
import time

import pandas as pd

from shimpaku import service
from shimpaku.analytics import db as adb
from shimpaku.analytics.sle import APP, FABRIC, summarise
from shimpaku.config import settings


log = logging.getLogger("shimpaku.providers")


def register_all(store) -> None:
    """`store`: the daemon's single read-write Store (CONTRACT "Process model" — DuckDB allows
    one read-write process; these providers read through its existing connection)."""

    def _cursor():
        return adb.connection(store).cursor()

    async def sle_get_summary(site: str, window_min: int = 60) -> dict:
        with adb.lock_of(store):
            cur = _cursor()
            try:
                end = pd.Timestamp.now(tz="UTC").tz_localize(None)
                units = adb.units(cur, end - pd.Timedelta(minutes=window_min), end)
            finally:
                cur.close()
        # `units.site` is each unit's own evidence["site"]; without this filter a request for one
        # site (e.g. "campus") silently included every other site's device-minutes too — caught
        # live (2026-10-24): /api/sle?site=campus showed a fabric adjacency event that never
        # touched r1/r2/sw1.
        if "site" in units.columns:
            units = units[units["site"] == site]
        start = end - pd.Timedelta(minutes=window_min)
        return {"site": site, "window_min": window_min,
                "sle": {s: summarise(units, s).as_dict() for s in (FABRIC, APP)},
                "series": {s: _series(units, s, window_min) for s in (FABRIC, APP)},
                "changes": _changes(site, start, end)}

    def _series(units, sle: str, window_min: int) -> list[dict]:
        """Per-bucket success rate from the same units the summary counts (≤ ~60 points)."""
        u = units[units["sle"] == sle] if "sle" in units.columns else units
        if u.empty:
            return []
        step = max(1, window_min // 60)
        b = pd.to_datetime(u["minute"]).dt.floor(f"{step}min")
        g = u.assign(bucket=b, good=(u["status"] == "good")).groupby("bucket")["good"].agg(["sum", "count"])
        return [{"t": ts.tz_localize("UTC").timestamp() if ts.tzinfo is None else ts.timestamp(),
                 "value": round(100.0 * float(r["sum"]) / float(r["count"]), 1), "n": int(r["count"])}
                for ts, r in g.iterrows()]

    def _changes(site: str, start, end) -> list[dict]:
        """Faults (incidents opened) and actions in the window, from the stores; nothing synthesised."""
        out: list[dict] = []
        t0, t1 = start.tz_localize("UTC").timestamp(), end.tz_localize("UTC").timestamp()
        with adb.lock_of(store):
            cur = _cursor()
            try:
                rows = cur.execute("SELECT incident_id, t_open, root_causes FROM incident WHERE t_open >= ? AND t_open < ?",
                                   [start, end]).fetchall()
            finally:
                cur.close()
        for iid, t_open, rc in rows:
            if not str(iid).endswith(f"-{site}"):
                continue
            top = (json.loads(rc) if rc else [{}]) or [{}]
            out.append({"t": pd.Timestamp(t_open).tz_localize("UTC").timestamp(), "kind": "fault",
                        "text": f"{iid}: {top[0].get('fault_class', 'incident')} {top[0].get('entity', '')}".strip(),
                        "device": None})
        try:
            from shimpaku.actions.engine import engine
            from shimpaku import devices as _dev
            nodes = {n.name for n in _dev.inventory().nodes.values() if n.site == site}
            for a in engine().list(None):
                if a.get("device") not in nodes:
                    continue
                for c in a.get("commits") or []:
                    if t0 <= float(c.get("ts") or 0) < t1:
                        out.append({"t": float(c["ts"]), "kind": "commit", "device": a.get("device"),
                                    "text": c.get("comment") or a["id"]})
                if t0 <= float(a.get("created") or 0) < t1:
                    out.append({"t": float(a["created"]), "kind": "action", "device": a.get("device"),
                                "text": f"{a['id']} {a.get('kind')} → {a.get('status')}"})
        except Exception:  # noqa: BLE001  # the timeline is optional; never fail the summary for it
            log.debug("changes: actions unavailable", exc_info=True)
        return sorted(out, key=lambda c: c["t"])

    def _inc_row(r) -> dict:
        return {"incident_id": r[0], "t_open": str(r[1]), "t_detect": str(r[2]) if r[2] else None,
                "t_close": str(r[3]) if r[3] else None, "sle": r[4],
                "root_causes": json.loads(r[5]) if r[5] else [], "status": r[6] if len(r) > 6 else None}

    async def sle_get_blast_radius(incident_id: str) -> dict:
        with adb.lock_of(store):
            cur = _cursor()
            try:
                row = cur.execute(
                    "SELECT incident_id, blast_radius, root_causes FROM incident WHERE incident_id = ?",
                    [incident_id],
                ).fetchone()
            finally:
                cur.close()
        if row is None:
            return {"available": False, "reason": f"no incident {incident_id!r}"}
        return {"incident_id": row[0], "blast_radius": json.loads(row[1]) if row[1] else None,
                "root_causes": json.loads(row[2]) if row[2] else []}

    async def incident_list(status: str | None = None) -> dict:
        with adb.lock_of(store):
            cur = _cursor()
            try:
                sql = "SELECT incident_id, t_open, t_detect, t_close, sle, root_causes, status FROM incident"
                if status:
                    rows = cur.execute(sql + " WHERE status = ? ORDER BY t_open DESC LIMIT 200", [status]).fetchall()
                else:
                    rows = cur.execute(sql + " ORDER BY t_open DESC LIMIT 200").fetchall()
            finally:
                cur.close()
        return {"incidents": [_inc_row(r) for r in rows]}

    async def incident_get(incident_id: str) -> dict:
        with adb.lock_of(store):
            cur = _cursor()
            try:
                row = cur.execute(
                    "SELECT incident_id, t_open, t_detect, t_close, sle, root_causes, status FROM incident "
                    "WHERE incident_id = ?", [incident_id],
                ).fetchone()
            finally:
                cur.close()
        if row is None:
            raise KeyError(incident_id)
        return _inc_row(row)

    async def probe_run(target: str, kind: str = "ping") -> dict:
        """A live synthetic probe, run on demand (not the periodic RPM probes)."""
        import subprocess

        if kind != "ping":
            return {"available": False, "reason": f"probe kind {kind!r} not implemented in v1"}
        t0 = time.monotonic()
        r = subprocess.run(["ping", "-c", "3", "-W", "2", target], capture_output=True, text=True, timeout=10)
        loss = 100.0
        for line in r.stdout.splitlines():
            if "packet loss" in line:
                loss = float(line.split("%")[0].split()[-1])
        return {"target": target, "kind": kind, "ok": r.returncode == 0, "loss_pct": loss,
                "elapsed_s": round(time.monotonic() - t0, 3)}

    async def bench_results() -> dict:
        p = settings.data / "bench" / "results.json"
        if not p.exists():
            return {"available": False, "reason": "no benchmark results published yet"}
        return json.loads(p.read_text())

    service.register_provider("sle_get_summary", sle_get_summary)
    service.register_provider("sle_get_blast_radius", sle_get_blast_radius)
    service.register_provider("probe_run", probe_run)
    service.register_provider("incident_list", incident_list)
    service.register_provider("incident_get", incident_get)
    service.register_provider("bench_results", bench_results)
