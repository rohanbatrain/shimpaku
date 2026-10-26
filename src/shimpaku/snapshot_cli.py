"""`shimpaku snapshot export` — record the live system for the public read-only replay.

Run on the lab host against the running daemon. It reads every route the dashboard reads, over
loopback REST, and writes one JSON file that `SHIMPAKU_REPLAY=<file>` serves (`api/replay.py`).

The file is meant to be published, so it is redacted on the way out:
- management addresses are dropped (`mgmt_ip`), home paths become `~`;
- every `--redact TERM` (host names, user names) becomes `<redacted>`, case-insensitively;
- the export refuses to write if any redacted term survives anywhere in the output.

    shimpaku snapshot export --out snapshot.json --redact "$(uname -n)" --redact "$USER"
"""

from __future__ import annotations

import json
import re
import time
from importlib.metadata import version
from pathlib import Path
from typing import Any

import httpx
import typer

app = typer.Typer(help="Export the live system for the public read-only replay.", no_args_is_help=True)

SITES = ("campus", "fabric")
WINDOWS = (60, 1440, 10080)  # the dashboard's 1 h / 24 h / 7 d
DROP_KEYS = {"mgmt_ip"}
HOME = re.compile(r"/(?:home|Users)/[^/\s\"']+")


def scrub(obj: Any, terms: list[str]) -> Any:
    if isinstance(obj, dict):
        return {k: scrub(v, terms) for k, v in obj.items() if k not in DROP_KEYS}
    if isinstance(obj, list):
        return [scrub(v, terms) for v in obj]
    if isinstance(obj, str):
        s = HOME.sub("~", obj)
        for t in terms:
            s = re.sub(re.escape(t), "<redacted>", s, flags=re.IGNORECASE)
        return s
    return obj


def leaks(blob: str, terms: list[str]) -> list[str]:
    low = blob.lower()
    return [t for t in terms if t.lower() in low] + (["mgmt_ip"] if '"mgmt_ip"' in blob else [])


def collect(get) -> dict:
    """``get(path, **params) -> dict``; one call per dashboard route."""
    snap: dict[str, Any] = {"format": 1, "taken_at": time.time(), "version": version("shimpaku")}
    snap["inventory"] = get("/api/inventory")
    snap["sle"] = {s: {str(w): get("/api/sle", site=s, window=w) for w in WINDOWS} for s in SITES}
    snap["incidents"] = get("/api/incidents")
    ids = [i.get("incident_id") or i["id"] for i in snap["incidents"].get("incidents", [])]
    snap["incident"] = {i: get(f"/api/incidents/{i}") for i in ids}
    snap["blast"] = {i: get(f"/api/incidents/{i}/blast-radius") for i in ids}
    snap["actions"] = get("/api/actions")
    snap["action"] = {a["id"]: get(f"/api/actions/{a['id']}") for a in snap["actions"].get("actions", [])}
    snap["topology"] = get("/api/topology")
    snap["audit"] = get("/api/audit", limit=1000)
    snap["audit_verify"] = get("/api/audit/verify")
    snap["bench"] = get("/api/bench/results")
    try:  # the Hardware page, as a visitor sees it (the export is anonymous: no hypervisor detail)
        snap["hardware"] = get("/api/hardware")
    except Exception:  # noqa: BLE001
        pass
    try:  # a daemon older than the heatmap has no such route; the replay then says "not in snapshot"
        snap["probes"] = {s: {str(w): get("/api/probes/heatmap", site=s, window=w) for w in WINDOWS} for s in SITES}
    except Exception:  # noqa: BLE001
        pass
    try:  # Virga live history and catalogue (allow-listed payloads); the replay shows them, never injects
        snap["virga"] = {"trials": get("/api/virga/trials"), "catalogue": get("/api/virga/catalogue")}
    except Exception:  # noqa: BLE001
        pass
    return snap


@app.command("export")
def export(
    out: Path = typer.Option(..., help="Snapshot file to write."),
    base: str = typer.Option("http://127.0.0.1:8780", help="The running daemon (loopback)."),
    redact: list[str] = typer.Option([], help="A term to replace with <redacted>; repeatable."),
) -> None:
    """Read every dashboard route from the live daemon and write a redacted snapshot."""
    terms = [t for t in redact if t.strip()]
    with httpx.Client(base_url=base, timeout=60) as c:
        def get(path: str, **params: Any) -> dict:
            r = c.get(path, params=params)
            r.raise_for_status()
            return r.json()

        snap = collect(get)
    if not snap["audit_verify"].get("ok"):
        typer.echo(f"audit chain does not verify on the live system: {snap['audit_verify']}", err=True)
        raise typer.Exit(1)
    clean = scrub(snap, terms)
    blob = json.dumps(clean, indent=1, default=str)
    found = leaks(blob, terms)
    if found:
        typer.echo(f"refusing to write: {found} still present after redaction", err=True)
        raise typer.Exit(1)
    out.write_text(blob)
    typer.echo(f"wrote {out}: {len(snap['action'])} actions, {len(snap['incident'])} incidents, "
               f"{len(snap['audit'].get('rows', []))} audit rows, {len(blob) // 1024} KiB")
