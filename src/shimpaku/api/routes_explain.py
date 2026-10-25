"""Explain views (``/api/explain/*``): the guardrail funnel, the audit hash chain and the blast-radius
map. Read-only compositions of existing service reads; nothing here writes or touches a device.

``app.py`` mounts it with ``app.include_router(routes_explain.router(call, reader))``.

Honesty: every count is computed from stored action rows, audit rows or incident payloads; the
tamper test recomputes real hashes over an in-memory *copy* of real rows (the log is never
written). Guest safety: callers are reported by kind only (agent / assistant / tester /
operator), never by name or token; reasons are scrubbed of addresses and lease holders; audit
rows carry the actor's role only; management addresses are never read.
"""

from __future__ import annotations

import copy
import json
import re
import time
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException

CALLER_KINDS = ("agent", "assistant", "tester", "operator")
WINDOWS = {"1h": 3600, "24h": 86400, "7d": 7 * 86400, "all": 0}
MAX_IDS = 200

LABELS = {
    "proposed": "Proposed", "passed": "Passed guardrails", "awaiting": "Awaiting approval",
    "automatic": "Self-drive", "waiting": "Still waiting", "approved": "Approved",
    "rejected": "Rejected", "cancelled": "Cancelled", "queued": "Approved, not yet run",
    "executed": "Executed", "running": "Running", "validating": "Validating", "verified": "Verified",
    "recurred": "Recurred", "failed": "Failed", "rolled_back": "Rolled back",
}
_RULE = re.compile(r"^(G\d+|T\d+)\b")


# ---- guardrail funnel ------------------------------------------------------------
def caller_kind(c: Any) -> str:
    """agent | assistant | tester | operator | other, from the stored caller (never its name)."""
    if not isinstance(c, dict):
        return "other"
    kind, role = str(c.get("kind") or ""), str(c.get("role") or "")
    if kind == "assistant":
        return "assistant"
    if role == "tester":
        return "tester"
    if role == "operator" or kind == "dashboard":
        return "operator"
    if kind == "mcp" or role == "agent":
        return "agent"
    return "other"


def first_rule(policy: Any) -> str:
    for r in (policy or {}).get("reasons") or [] if isinstance(policy, dict) else []:
        m = _RULE.match(str(r))
        if m:
            return m.group(1)
    return "G?"


def safe_reason(text: Any) -> str:
    """A guardrail reason fit for any viewer: no addresses, no other principal's identity."""
    from shimpaku.service import scrub_hosts

    s = re.sub(r"(leased by )\S.*$", r"\1another session", str(text))
    return scrub_hosts(s)[:180]


def path_of(a: dict) -> list[str]:
    """The funnel path one stored action took, from its status, timeline and commits."""
    status = a.get("status")
    tl = [t for t in a.get("timeline") or [] if isinstance(t, dict)]
    seen = [t.get("to") for t in tl]
    notes = " ".join(str(t.get("note") or "") for t in tl).lower()
    p = ["proposed"]
    if status == "blocked":
        return p + [f"blocked:{first_rule(a.get('policy'))}"]
    if status == "open":                       # mid-propose (policy not decided yet)
        return p
    p.append("passed")
    if "awaiting_approval" in seen or status == "awaiting_approval":
        p.append("awaiting")
        decision = next((s for s in seen if s in ("approved", "rejected", "cancelled")), None)
        if decision is None:
            return p + ["waiting" if status == "awaiting_approval" else (status or "waiting")]
        p.append(decision)
        if decision != "approved":
            return p
    elif "automatic" in seen or status == "automatic":
        p.append("automatic")
    else:
        return p + [status] if status in LABELS else p
    if "running" in seen or a.get("commits"):
        p.append("executed")
    elif status == "failed":
        return p + ["failed"]
    else:
        return p + ["queued"]
    if status == "failed" and ("rolled back" in notes or "rollback" in notes):
        return p + ["rolled_back"]
    if status in ("verified", "recurred", "failed", "validating", "running"):
        p.append(status)
    return p


def funnel(rows: list[dict], caller: str = "all", window_s: int = 0, now: float | None = None) -> dict:
    from shimpaku.api.view import _GUARDRAIL_NAMES

    now = now or time.time()
    since = now - window_s if window_s else None
    by_caller = {k: 0 for k in (*CALLER_KINDS, "other")}
    picked = []
    for a in rows:
        if not isinstance(a, dict):
            continue
        created = a.get("created")
        if since is not None and (not isinstance(created, (int, float)) or created < since):
            continue
        ck = caller_kind(a.get("caller"))
        by_caller[ck] += 1
        if caller in ("all", ck):
            picked.append((a, ck))
    nodes: dict[str, dict] = {}
    links: dict[tuple[str, str], dict] = {}
    examples: dict[str, list[str]] = {}
    for a, ck in picked:
        path = path_of(a)
        aid = str(a.get("id"))
        for n in path:
            nd = nodes.setdefault(n, {"id": n, "count": 0, "ids": []})
            nd["count"] += 1
            if len(nd["ids"]) < MAX_IDS:
                nd["ids"].append(aid)
        for s, t in zip(path, path[1:]):
            lk = links.setdefault((s, t), {"source": s, "target": t, "count": 0, "ids": []})
            lk["count"] += 1
            if len(lk["ids"]) < MAX_IDS:
                lk["ids"].append(aid)
        if path[-1].startswith("blocked:"):
            rule = path[-1].split(":", 1)[1]
            reasons = [r for r in (a.get("policy") or {}).get("reasons") or [] if str(r).startswith(rule)]
            ex = examples.setdefault(rule, [])
            for r in reasons[:1]:
                s = safe_reason(r)
                if s not in ex and len(ex) < 3:
                    ex.append(s)
    for n in nodes.values():
        nid = n["id"]
        if nid.startswith("blocked:"):
            rule = nid.split(":", 1)[1]
            n.update(label=f"Blocked at {rule}", rule=rule, rule_name=_GUARDRAIL_NAMES.get(rule) or ("caller-role gate: what this caller's role may propose at all" if rule == "T0" else None),
                     examples=examples.get(rule, []), stage="blocked")
        else:
            n.update(label=LABELS.get(nid, nid), stage=nid)
    return {"generated_at": now, "caller": caller, "window_s": window_s, "total": len(picked),
            "by_caller": by_caller, "nodes": sorted(nodes.values(), key=lambda n: -n["count"]),
            "links": sorted(links.values(), key=lambda lk: -lk["count"])}


# ---- audit chain ----------------------------------------------------------------
def _role(actor: Any) -> str | None:
    if isinstance(actor, str):
        try:
            actor = json.loads(actor)
        except ValueError:
            return None
    if isinstance(actor, dict):
        return str(actor.get("role") or actor.get("kind") or "") or None
    return None


def block_of(r: dict, ok: bool | None = None) -> dict:
    """One audit row as a chain block: no args, no actor identity — role only."""
    return {"seq": r.get("seq"), "ts": r.get("ts"), "tool": r.get("tool"), "kind": r.get("kind"),
            "role": _role(r.get("actor")), "status": r.get("status"),
            "prev_hash": r.get("prev_hash"), "row_hash": r.get("row_hash"), "ok": ok}


def walk(rows: list[dict], prev: str | None) -> list[dict]:
    """Recompute each row's hash and link (rows oldest first). ``prev`` = the hash before rows[0]
    (None: trust rows[0]'s own prev_hash). Returns per-row {seq, hash_ok, link_ok, recomputed}."""
    from shimpaku.store import audit_hash

    out = []
    for r in rows:
        rec = audit_hash(r)
        link_ok = True if prev is None else r.get("prev_hash") == prev
        out.append({"seq": r.get("seq"), "hash_ok": rec == r.get("row_hash"), "link_ok": link_ok, "recomputed": rec})
        prev = r.get("row_hash")
    return out


def tamper(rows: list[dict], seq: int, rehash: bool) -> dict:
    """Edit one row of an in-memory COPY (its status), optionally re-hash it as a forger would, and
    walk the copy. Returns where the chain breaks. The caller's rows are not modified."""
    from shimpaku.store import audit_hash

    work = copy.deepcopy(sorted(rows, key=lambda r: r["seq"]))
    target = next((r for r in work if r["seq"] == seq), None)
    if target is None:
        raise KeyError(seq)
    before = target.get("status")
    target["status"] = "ok (edited)" if before != "ok (edited)" else "refused"
    if rehash:
        target["row_hash"] = audit_hash(target)
    res = walk(work, None)
    broken = next((x for x in res if not (x["hash_ok"] and x["link_ok"])), None)
    why = None
    if broken:
        why = ("row content no longer matches its stored row_hash" if not broken["hash_ok"]
               else "prev_hash no longer matches the row before it (the edited row was re-hashed)")
    return {"edited_seq": seq, "field": "status", "before": before, "after": target["status"], "rehash": rehash,
            "first_bad_seq": broken["seq"] if broken else None, "why": why,
            "blocks": [{**block_of(r), **x} for r, x in zip(work, res)]}


# ---- blast radius → map -----------------------------------------------------------
def _phys(ifn: str) -> str:
    return ifn.split(".", 1)[0]


def map_entities(entities: list[tuple[str, str]], nodes: set[str], links: list[dict]) -> dict:
    """Entities (source, text) → highlighted nodes / links. ``node`` → that node; ``node:ifname``
    (any extra parts) → the node and the intended link on that port; text naming two nodes on one
    link (``leaf1 ↔ spine2``) → that link."""
    hit_nodes: dict[str, list[str]] = {}
    hit_links: dict[str, list[str]] = {}
    unmapped = []
    for src, ent in entities:
        named, ports, last = [], set(), None
        for tok in re.findall(r"[A-Za-z0-9-]+(?:/[0-9]+)*(?:\.[0-9]+)?", str(ent)):
            if tok in nodes:
                named.append(tok)
                last = tok
            elif "/" in tok and last:          # a port belongs to the node named just before it
                ports.add((last, _phys(tok)))
        got = False
        for n in dict.fromkeys(named):
            hit_nodes.setdefault(n, []).append(src)
            got = True
        for lk in links:
            ends = {(lk.get("a"), lk.get("a_if")), (lk.get("b"), lk.get("b_if"))}
            on_port = bool(ends & ports)
            both = lk.get("a") in named and lk.get("b") in named
            if on_port or both:
                hit_links.setdefault(lk["id"], []).append(src)
                got = True
        if not got:
            unmapped.append(str(ent)[:120])
    return {"nodes": [{"name": k, "why": sorted(set(v))} for k, v in hit_nodes.items()],
            "links": [{"id": k, "why": sorted(set(v))} for k, v in hit_links.items()],
            "unmapped": sorted(set(unmapped))[:20]}


def router(call: Callable[..., Awaitable[Any]], reader: Callable[..., Any]) -> APIRouter:
    r = APIRouter()

    async def raw_actions() -> tuple[list[dict], bool]:
        out = await call("action_list")
        demo = bool(isinstance(out, dict) and out.get("demo"))
        if demo:  # demo rows are view-shaped; the funnel reads the engine's shape
            from shimpaku.api import demo_explain
            return demo_explain.engine_rows(out.get("actions") or []), True
        return [a for a in (out or {}).get("actions") or [] if isinstance(a, dict)], False

    @r.get("/api/explain/funnel")
    async def explain_funnel(caller: str = "all", window: str = "7d", p=Depends(reader)) -> dict:
        """Every stored action proposal through the guardrails and the write path."""
        if caller not in ("all", *CALLER_KINDS):
            raise HTTPException(422, f"caller must be one of all, {', '.join(CALLER_KINDS)}")
        if window not in WINDOWS:
            raise HTTPException(422, f"window must be one of {', '.join(WINDOWS)}")
        rows, demo = await raw_actions()
        out = funnel(rows, caller, WINDOWS[window])
        out["window"] = window
        return {**out, **({"demo": True} if demo else {})}

    async def chain_rows(after: int | None, before: int | None, limit: int) -> dict:
        out = await call("audit_chain", after=after, before=before, limit=limit)
        if not isinstance(out, dict) or out.get("available") is False:
            raise HTTPException(503, (out or {}).get("reason") or "audit log not available")
        return out

    @r.get("/api/explain/audit/chain")
    async def explain_chain(before: int | None = None, limit: int = 20, p=Depends(reader)) -> dict:
        """A page of the hash chain (newest first), each block re-hashed and linked to the one before."""
        limit = max(1, min(int(limit), 100))
        out = await chain_rows(None, before, limit + 1)  # one extra (older) row checks the oldest link
        rows = sorted(out.get("rows") or [], key=lambda x: x["seq"])
        older = rows[0] if len(rows) > limit else None
        page = rows[1:] if older else rows
        res = walk(page, older["row_hash"] if older else (page[0].get("prev_hash") if page else None))
        if not older and page and page[0]["seq"] == 1:
            from shimpaku.store import GENESIS
            res[0]["link_ok"] = page[0].get("prev_hash") == GENESIS
        blocks = [{**block_of(x), **v, "ok": v["hash_ok"] and v["link_ok"]} for x, v in zip(page, res)]
        verify = await call("audit_verify")
        return {"blocks": list(reversed(blocks)), "total": out.get("total"),
                "next_before": page[0]["seq"] if page and page[0]["seq"] > 1 else None,
                "verify": {k: verify.get(k) for k in ("ok", "rows", "first_bad_seq", "why") if k in verify},
                "checked_at": time.time(), **({"demo": True} if out.get("demo") else {})}

    @r.get("/api/explain/audit/tamper")
    async def explain_tamper(seq: int, rehash: bool = False, p=Depends(reader)) -> dict:
        """Tamper test on an in-memory copy of rows seq-2 … seq+3: never touches the real log."""
        out = await chain_rows(seq - 3 if seq > 3 else None, seq + 3, 7)
        rows = [x for x in out.get("rows") or [] if isinstance(x, dict)]
        try:
            res = tamper(rows, seq, rehash)
        except KeyError:
            raise HTTPException(404, f"no audit row {seq}") from None
        return {**res, "copy_only": True, **({"demo": True} if out.get("demo") else {})}

    @r.get("/api/explain/incidents/{incident_id}/blast-map")
    async def explain_blast_map(incident_id: str, p=Depends(reader)) -> dict:
        """The incident's root-cause entities and blast radius mapped onto topology nodes / links."""
        from shimpaku.api.routes_viz import intended_links, nodes_of

        inc = await call("incident_get", incident_id)
        try:
            br = await call("sle_get_blast_radius", incident_id)
        except HTTPException:
            br = {}
        topo, inv = await call("topo_get_neighbors"), await call("inventory_list")
        names = {n["name"] for n in nodes_of(inv if isinstance(inv, dict) else {})}
        links = intended_links(topo if isinstance(topo, dict) else {})
        ents: list[tuple[str, str]] = []
        for rc in (inc.get("root_causes") or []) + ((br or {}).get("root_causes") or []):
            if isinstance(rc, dict) and rc.get("entity"):
                ents.append(("root cause", str(rc["entity"])))
        b = (br or {}).get("blast_radius") if isinstance((br or {}).get("blast_radius"), dict) else None
        for a in (b or {}).get("affected") or []:
            ents.append(("blast radius", str(a)))
        for a in (br or {}).get("affected") or []:      # demo / view shape
            if isinstance(a, dict):
                ents.append(("blast radius", f"{a.get('id')} {a.get('name')} {a.get('detail') or ''}"))
        if not ents and inc.get("title"):
            ents.append(("incident title", str(inc["title"])))
        m = map_entities(ents, names, links)
        return {"incident_id": incident_id, "title": inc.get("title") or inc.get("incident_id"), **m,
                "entities": len(ents), **({"demo": True} if inc.get("demo") else {})}

    return r
