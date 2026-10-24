"""Replay (``SHIMPAKU_REPLAY=<snapshot.json>``): the CONTRACT read functions over a snapshot
exported from a live daemon by ``shimpaku snapshot export``.

This is what a public host serves. It holds no device credentials and opens no connection to the
lab: every answer is a row recorded from the real system at ``taken_at``. Nothing is re-timed or
re-scored. Writes are refused, because the write path is the part that must never face the
internet; the assistant is off, because a public endpoint must not spend the LLM gateway key.

Unlike ``demo`` (canned, lab-shaped data for dashboard development), every number here is real.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import AsyncIterator

REPLAY = True
READ_ONLY = "read-only replay: the write path runs only against the private lab (see DEMO.md)"

_snap: dict | None = None


def load(path: Path | str | None = None) -> dict:
    """Load the snapshot once; ``path`` defaults to ``$SHIMPAKU_REPLAY``."""
    global _snap
    if path is not None or _snap is None:
        p = Path(path or os.environ["SHIMPAKU_REPLAY"])
        snap = json.loads(p.read_text())
        if snap.get("format") != 1:
            raise ValueError(f"{p}: unsupported snapshot format {snap.get('format')!r}")
        _snap = snap
    return _snap


def info() -> dict:
    s = load()
    return {"taken_at": s["taken_at"], "version": s.get("version")}


def _tag(d: dict) -> dict:
    return {**d, "replay": True}


def _missing(what: str) -> dict:
    return {"error": {"code": "not_found", "message": f"{what} is not in this snapshot"}}


async def inventory_list(site: str | None = None) -> dict:
    inv = load()["inventory"]
    key = "nodes" if "nodes" in inv else "devices"
    return _tag({**inv, key: [d for d in inv[key] if not site or d.get("site") == site]})


async def sle_get_summary(site: str, window_min: int = 60) -> dict:
    by_site = load()["sle"].get(site)
    if not by_site:
        return _missing(f"site {site}")
    # the snapshot holds the dashboard's windows; answer with the nearest one at or above
    have = sorted(int(w) for w in by_site)
    w = next((x for x in have if x >= window_min), have[-1])
    return _tag(by_site[str(w)])


async def incident_list(status: str | None = None) -> dict:
    rows = load()["incidents"]["incidents"]
    return _tag({"incidents": [i for i in rows if not status or i.get("status") == status]})


async def incident_get(incident_id: str) -> dict:
    i = load()["incident"].get(incident_id)
    return _tag(i) if i else _missing(f"incident {incident_id}")


async def sle_get_blast_radius(incident_id: str) -> dict:
    b = load()["blast"].get(incident_id)
    return _tag(b) if b else _missing(f"blast radius for {incident_id}")


async def action_list(status: str | None = None) -> dict:
    rows = load()["actions"]["actions"]
    return _tag({"actions": [a for a in rows if not status or a.get("status") == status]})


async def action_get(action_id: str) -> dict:
    a = load()["action"].get(action_id)
    return _tag(a) if a else _missing(f"action {action_id}")


async def audit_query(tool: str | None = None, action_id: str | None = None, status: str | None = None,
                      limit: int = 100, **_: object) -> dict:
    rows = load()["audit"]["rows"]

    def keep(r: dict) -> bool:
        return ((not tool or r.get("tool") == tool)
                and (not action_id or action_id in json.dumps(r, default=str))
                and (not status or r.get("status") == status))

    return _tag({"rows": [r for r in rows if keep(r)][:limit]})


async def audit_verify() -> dict:
    """The verifier's result at export time. Rows here are redacted copies, so the chain is not
    recomputed on them; ``verified_at`` says when the live verifier ran."""
    s = load()
    return _tag({**s["audit_verify"], "verified_at": s["taken_at"]})


async def topo_get_neighbors(device: str | None = None) -> dict:
    t = load()["topology"]
    edges = t.get("edges", [])
    if device:
        edges = [e for e in edges if device in (e.get("a"), e.get("b"))]
    return _tag({**t, "edges": edges})


async def bench_results() -> dict:
    return _tag(load()["bench"])


async def _refuse(*_: object, **__: object) -> dict:
    raise PermissionError(READ_ONLY)


action_propose = action_approve = action_reject = action_cancel = _refuse


async def assistant_ask(question: str, principal: str, context: dict | None = None) -> AsyncIterator[dict]:
    yield {"type": "meta", "model": "off in replay", "replay": True}
    for word in ("Shim is off on the public replay: it needs the private LLM gateway. "
                 "The recorded evaluation is in docs/build/assistant.md.").split(" "):
        yield {"type": "token", "text": word + " "}
    yield {"type": "done"}
