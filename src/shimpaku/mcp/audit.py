"""Audit middleware storage: the store's hash-chained `audit` table plus an `audit.jsonl` mirror.

The chain itself is `shimpaku.store.StateDB.audit_append` (row_hash = sha256 over the canonical
row including prev_hash, `BEGIN IMMEDIATE`, linear across processes). We map R7 §2.5's fields
onto it:

| R7 field | StateDB column |
|---|---|
| caller (token id / OS user) | actor (JSON text) |
| transport + method | kind, e.g. `mcp:http:tools/call` |
| tool | tool |
| args (redacted) | args |
| tier, policy_decision, action_id, incident_id, result_status, latency_ms, device_commit, client_info | result |
| result_status | status |

`verify()` walks the SQLite chain and checks every JSONL line against both the chain and its own
hash, so an edited row in either copy is found.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

from shimpaku.store import StateDB, audit_hash

_SECRET_KEYS = re.compile(r"pass|secret|token|key|auth|cookie|credential", re.I)


def redact(v: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "…"
    if isinstance(v, dict):
        return {k: ("[redacted]" if _SECRET_KEYS.search(str(k)) else redact(x, depth + 1)) for k, x in v.items()}
    if isinstance(v, list):
        return [redact(x, depth + 1) for x in v[:50]]
    if isinstance(v, str):
        return v[:2000]
    return v


def _loads(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


class AuditLog:
    def __init__(self, db: Path | str | StateDB, jsonl: Path):
        self.state = db if isinstance(db, StateDB) else StateDB(db).init()
        self.jsonl = Path(jsonl)
        self.jsonl.parent.mkdir(parents=True, exist_ok=True)
        self._mu = threading.Lock()

    def append(self, *, caller: Any = None, client_info: Any = None, transport: str | None = None,
               method: str = "tools/call", tool: str | None = None, args: Any = None, tier: str | None = None,
               policy_decision: Any = None, action_id: str | None = None, incident_id: str | None = None,
               result_status: str = "ok", latency_ms: float | None = None, device_commit: Any = None) -> dict:
        result = {"tier": tier, "policy_decision": policy_decision, "action_id": action_id,
                  "incident_id": incident_id, "result_status": result_status, "latency_ms": latency_ms,
                  "device_commit": device_commit, "client_info": client_info}
        with self._mu:
            row = self.state.audit_append(
                actor=json.dumps(redact(caller), sort_keys=True, default=str) if caller is not None else None,
                kind=f"mcp:{transport or '?'}:{method}" if tool != "executor" else "executor",
                tool=tool, args=redact(args), result=redact(result),
                status=(result_status or "ok")[:40])
            with self.jsonl.open("a") as f:
                f.write(json.dumps(row, sort_keys=True) + "\n")
        return row

    def query(self, tool: str | None = None, action_id: str | None = None, since: float | None = None,
              status: str | None = None, limit: int = 100) -> list[dict]:
        from datetime import datetime, timezone

        since_iso = datetime.fromtimestamp(since, timezone.utc).isoformat() if since else None
        limit = max(1, min(int(limit), 1000))
        rows = self.state.audit_query(since=since_iso, tool=tool, limit=5000 if (action_id or status) else limit)
        out = []
        for r in rows:
            d = dict(r)
            d["actor"] = _loads(d.get("actor"))
            d["args"] = _loads(d.get("args"))
            d["result"] = _loads(d.get("result")) or {}
            res = d["result"] if isinstance(d["result"], dict) else {}
            if action_id and res.get("action_id") != action_id:
                continue
            if status and d.get("status") != status:
                continue
            d.update({k: res.get(k) for k in ("tier", "policy_decision", "action_id", "result_status")})
            out.append(d)
        return out[:limit]

    def verify(self) -> dict:
        ok, bad = self.state.audit_verify()
        if not ok:
            return {"ok": False, "first_bad_seq": bad, "why": "sqlite chain broken (row edited, removed or reordered)"}
        # cross-check the JSONL mirror
        chain = {r["seq"]: r["row_hash"] for r in self.state.audit_query(limit=10**9)}
        if self.jsonl.exists():
            for n, line in enumerate(self.jsonl.read_text().splitlines(), 1):
                try:
                    j = json.loads(line)
                except ValueError:
                    return {"ok": False, "jsonl_line": n, "why": "unparseable JSONL line"}
                if audit_hash(j) != j.get("row_hash") or chain.get(j.get("seq")) != j.get("row_hash"):
                    return {"ok": False, "jsonl_line": n, "first_bad_seq": j.get("seq"),
                            "why": "JSONL copy disagrees with the chain"}
        return {"ok": True, "rows": len(chain)}


def main() -> None:
    """`python -m shimpaku.mcp.audit` — verify the live chain and the JSONL mirror."""
    from shimpaku.config import settings

    res = AuditLog(settings.sqlite_path, settings.data / "audit.jsonl").verify()
    print(json.dumps(res))
    raise SystemExit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()
