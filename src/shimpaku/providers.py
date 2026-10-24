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
import time

import pandas as pd

from shimpaku import service
from shimpaku.analytics import db as adb
from shimpaku.analytics.sle import APP, FABRIC, summarise
from shimpaku.config import settings


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
        return {"site": site, "window_min": window_min,
                "sle": {s: summarise(units, s).as_dict() for s in (FABRIC, APP)}}

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
