"""How Virga learns what the system under test saw and did. Read-only; never the other way round.

    none                         nothing (the null system: every trial a miss, zero false positives)
    file:PATH                    JSONL appended by an agent or an MCP audit log. Lines are either R9
                                 events {"t": iso|epoch, "type": "incident"|"action"|"resolved", ...}
                                 or R5 incident objects {"incident_id", "t_detect", "root_causes", ...}
    shimpaku[:URL]               Shimpaku's REST API (CONTRACT.md): GET /api/incidents and
                                 GET /api/actions; URL defaults to http://127.0.0.1:8780. A bearer
                                 token is read from $VIRGA_SHIMPAKU_TOKEN when auth is on.

Everything is normalised to R5 §6.1 incidents: {incident_id, t_detect (epoch s), sle, root_causes:
[{entity, fault_class, score}], blast_radius: [entity], actions: [...]} and actions: {action_id, t,
tool, args, node, status}. Field names the daemon may use instead are mapped in `norm_incident`.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path


def epoch(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) / (1000.0 if v > 1e12 else 1.0)
    s = str(v).strip()
    try:
        return float(s)
    except ValueError:
        pass
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def _first(d: dict, *keys, default=None):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _entity_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, dict):
        v = _first(v, "entities", "impacted", "items", default=[])
    out = []
    for x in v:
        if isinstance(x, str):
            out.append(x)
        elif isinstance(x, dict) and x.get("entity"):
            out.append(x["entity"])
        elif isinstance(x, dict) and x.get("id"):
            out.append(x["id"])
    return out


def norm_incident(d: dict) -> dict | None:
    t = epoch(_first(d, "t_detect", "detected_at", "opened_at", "t_open", "created_at", "start", "t"))
    if t is None:
        return None
    rcs = _first(d, "root_causes", "candidates", "suspects", default=None)
    if rcs is None and isinstance(d.get("root_cause"), dict):
        rcs = [d["root_cause"]]
    ranked = []
    for c in rcs or []:
        if isinstance(c, str):
            ranked.append({"entity": c, "fault_class": "unknown"})
        elif isinstance(c, dict):
            ranked.append({"entity": _first(c, "entity", "entity_id", "id", default="?"),
                           "fault_class": _first(c, "fault_class", "kind", "class", "classifier", default="unknown"),
                           "score": c.get("score")})
    ranked.sort(key=lambda c: -(c.get("score") or 0) if c.get("score") is not None else 0)
    return {"incident_id": str(_first(d, "incident_id", "id", default=f"anon-{t}")), "t_detect": t,
            "sle": _first(d, "sle", "sle_name"), "root_causes": ranked,
            "blast_radius": _entity_list(_first(d, "blast_radius", "impacted_entities", "impact")),
            "status": d.get("status")}


def norm_action(d: dict) -> dict | None:
    t = epoch(_first(d, "t", "executed_at", "committed_at", "updated_at", "created_at"))
    if t is None:
        return None
    args = d.get("args") or {}
    return {"action_id": str(_first(d, "action_id", "id", default=f"a-{t}")), "t": t,
            "tool": _first(d, "tool", "kind", default="?"), "args": args,
            "node": _first(d, "node", "device", default=args.get("node") or args.get("device")),
            "status": d.get("status", "executed")}


class Adapter:
    name = "none"

    def incidents(self, since: float = 0.0) -> list[dict]:
        return []

    def actions(self, since: float = 0.0) -> list[dict]:
        return []


class FileAdapter(Adapter):
    def __init__(self, path: str, name: str = "file"):
        self.path = Path(path).expanduser()
        self.name = name

    def _rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for ln in self.path.read_text().splitlines():
            ln = ln.strip()
            if ln:
                try:
                    row = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    out.append(row)
        return out

    def incidents(self, since: float = 0.0) -> list[dict]:
        out = []
        for r in self._rows():
            if r.get("type") not in (None, "incident"):
                continue
            n = norm_incident(r)
            if n and n["t_detect"] >= since:
                out.append(n)
        return out

    def actions(self, since: float = 0.0) -> list[dict]:
        out = []
        for r in self._rows():
            if r.get("type") == "action":
                n = norm_action(r)
                if n and n["t"] >= since:
                    out.append(n)
        return out


class ShimpakuAdapter(Adapter):
    name = "shimpaku"

    def __init__(self, base: str = "http://127.0.0.1:8780", timeout: float = 10.0):
        self.base = base.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str):
        import httpx

        headers = {}
        tok = os.environ.get("VIRGA_SHIMPAKU_TOKEN")
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
        r = httpx.get(self.base + path, headers=headers, timeout=self.timeout)
        r.raise_for_status()
        body = r.json()
        if isinstance(body, dict):
            body = _first(body, "items", "incidents", "actions", "data", default=[])
        return body

    def incidents(self, since: float = 0.0) -> list[dict]:
        out = []
        for d in self._get("/api/incidents"):
            n = norm_incident(d)
            if n and n["t_detect"] >= since:
                out.append(n)
        return out

    def actions(self, since: float = 0.0) -> list[dict]:
        out = []
        for d in self._get("/api/actions"):
            n = norm_action(d)
            if n and n["t"] >= since and n["status"] in ("executed", "confirmed", "validating", "validated",
                                                         "ai_validated", "verified", "done"):
                out.append(n)
        return out


def make(spec: str | None) -> Adapter:
    if not spec or spec == "none":
        return Adapter()
    if spec.startswith("file:"):
        return FileAdapter(spec[5:])
    if spec == "shimpaku" or spec.startswith("shimpaku:"):
        return ShimpakuAdapter(spec.split(":", 1)[1] if ":" in spec else "http://127.0.0.1:8780")
    raise ValueError(f"unknown adapter {spec!r} (none | file:PATH | shimpaku[:URL])")
