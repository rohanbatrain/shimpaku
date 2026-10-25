"""Read-only views for the Overview, Topology and Device pages (``/api/viz/*``).

Each route composes existing service reads; nothing here talks to a device directly or writes.
``app.py`` mounts it with ``app.include_router(routes_viz.router(call, reader))`` so the routes get
the app's own service caller (error mapping) and the same ``reader`` dependency (auth, CSRF,
tester leases) as every other GET.

Honesty rules (``api/view.py``): every value comes from a payload; what is not collected is
``None`` with a reason, never a guess. Guest safety: management addresses are never returned by
any route here (whoever asks), and the only port config read is the demo fixture port on
``sw1`` — the device a guest may already read config on (``mcp/server.py``) — reduced to one
boolean (is VLAN 999 a member).
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException

from shimpaku.api import view

TOPO_TTL_S = 8.0

# expected underlay protocols per intended link role (lab design: fabric = OSPF + BFD underlay,
# campus r1–r2 = the OSPF exercise, r1–sw1 = an L2 trunk with no routing protocol)
EXPECTED = {"fabric": ("ospf", "bfd"), "routed": ("ospf",), "trunk": ()}


def _scrub(text: Any) -> str:
    from shimpaku.service import scrub_hosts
    return scrub_hosts(str(text))[:200]


def role_of(name: str, kind: str | None, hint: str | None = None) -> str:
    """Glyph role from the TOML: vjunos-router → router; vjunos-switch → spine / leaf by name, else switch."""
    h = (hint or "").lower()
    k = (kind or "").lower()
    if "router" in k or h == "router":
        return "router"
    if name.startswith("spine") or h == "spine":
        return "spine"
    if name.startswith("leaf") or h == "leaf":
        return "leaf"
    return "switch"


def _phys(ifname: str | None) -> str | None:
    return ifname.split(".", 1)[0] if ifname else None


def nodes_of(inv: dict) -> list[dict]:
    """Inventory (raw service ``nodes`` or the view's ``devices``) without management addresses."""
    rows = inv.get("nodes") if isinstance(inv.get("nodes"), list) else inv.get("devices") or []
    out = []
    for n in rows:
        if not isinstance(n, dict) or not n.get("name"):
            continue
        kind = n.get("kind") or n.get("model")
        out.append({"name": n["name"], "site": n.get("site"), "kind": kind,
                    "role": role_of(n["name"], kind, n.get("role")),
                    "version": n.get("version"), "topology": n.get("topology")})
    return out


def intended_links(topo: dict) -> list[dict]:
    """One row per TOML link (both ends), from ``intended``; falls back to demo ``edges``."""
    links: dict[str, dict] = {}
    for e in topo.get("intended") or []:
        lid = e.get("link") or "-".join(sorted((e.get("device") or "", e.get("peer") or "")))
        row = links.get(lid)
        if row is None:
            links[lid] = {"id": lid, "a": e.get("device"), "a_if": e.get("ifname"), "b": e.get("peer"),
                          "b_if": None, "role": e.get("role")}
        elif row["b"] == e.get("device"):
            row["b_if"] = e.get("ifname")
    if not links:
        for e in topo.get("edges") or []:
            lid = "-".join(sorted((e.get("a") or "", e.get("b") or "")))
            links.setdefault(lid, {"id": lid, "a": e.get("a"), "a_if": e.get("a_if"), "b": e.get("b"),
                                   "b_if": e.get("b_if"), "role": e.get("role")})
    return list(links.values())


def _lldp_evidence(link: dict, lldp: list[dict]) -> str:
    ends = ((link["a"], link["a_if"], link["b"]), (link["b"], link["b_if"], link["a"]))
    for dev, ifn, peer in ends:
        if any(x.get("device") == dev and x.get("local_if") == ifn and x.get("remote_system") == peer for x in lldp):
            return "seen"
    active = {x.get("device") for x in lldp if x.get("local_if")}
    if active & {link["a"], link["b"]}:
        return "missing"     # LLDP answers on that device, but not for this port
    if all(any(x.get("device") == d and x.get("error") for x in lldp) for d in (link["a"], link["b"])):
        return "unreachable"
    return "not_reported"    # the device reports no LLDP neighbours at all (LLDP off there)


def _proto(routing: dict[str, dict], dev: str, ifn: str | None, key: str, state_key: str = "state") -> str | None:
    r = routing.get(dev)
    if not r or r.get("error"):
        return None
    hits = [x.get(state_key) for x in r.get(key) or [] if _phys(x.get("interface")) == ifn]
    if hits:
        return hits[0]
    return "absent"


def link_state(link: dict, lldp: list[dict], routing: dict[str, dict], ports: dict[str, dict]) -> dict:
    """up / degraded / down / unknown from whatever evidence exists; ``evidence`` says which."""
    ev: dict[str, Any] = {"lldp": _lldp_evidence(link, lldp) if lldp else None}
    expected = EXPECTED.get(link.get("role") or "", ())
    good, bad = [], []
    if ev["lldp"] == "seen":
        good.append("LLDP neighbour seen")
    elif ev["lldp"] == "missing":
        bad.append("LLDP neighbour missing")
    for side in ("a", "b"):
        dev, ifn = link[side], link.get(f"{side}_if")
        oper = (ports.get(dev) or {}).get(ifn)
        ev[f"oper_{side}"] = oper
        if oper == "down":
            ev["down"] = True
            bad.append(f"{dev} {ifn} oper down")
        elif oper == "up":
            good.append(f"{dev} {ifn} oper up")
        for proto, key, ok in (("ospf", "ospf", "Full"), ("bfd", "bfd", "Up")):
            st = _proto(routing, dev, ifn, key)
            ev[f"{proto}_{side}"] = st
            if st is None or (st == "absent" and proto not in expected):
                continue
            if st == ok:
                good.append(f"{dev} {proto.upper()} {st}")
            else:
                bad.append(f"{dev} {proto.upper()} {'neighbour absent' if st == 'absent' else st}")
    if ev.get("down"):
        state = "down"
    elif bad and good:
        state = "degraded"
    elif bad:
        state = "down"
    elif good:
        state = "up"
    else:
        state = "unknown"
    ev.pop("down", None)
    return {**link, "state": state, "evidence": ev, "good": good, "bad": bad}


def fixture_state(pc: dict | None) -> dict:
    from shimpaku.actions import fixture

    out = {"device": fixture.DEVICE, "ifname": fixture.IFNAME, "vlan": fixture.VLAN,
           "keep_vlan": fixture.KEEP_VLAN, "present": None, "oper": None, "admin": None, "reason": None}
    if not isinstance(pc, dict) or pc.get("available") is False or pc.get("error"):
        out["reason"] = (pc or {}).get("reason") or "the fixture port could not be read"
        return out
    if "vlan_present" in pc:  # demo stand-in
        out.update(present=pc["vlan_present"], oper=pc.get("oper"), admin=pc.get("admin"))
        return out
    text = "\n".join([pc.get("config_inherited") or "", pc.get("config_own") or "",
                      *(pc.get("vlan_lines") or []), *(pc.get("interface_range_lines") or [])])
    pat = re.compile(rf"(\bmembers\s+(\[[^\]]*\b)?{fixture.VLAN}\b|{re.escape(fixture.vlan_name(fixture.VLAN))}\b)")
    out.update(present=bool(pat.search(text)), oper=pc.get("oper"), admin=pc.get("admin"))
    return out


def _touches(node: str, incident: dict) -> bool:
    if node in re.split(r"[^A-Za-z0-9-]+", str(incident.get("title") or "")):
        return True
    for rc in incident.get("root_causes") or []:
        ent = str((rc or {}).get("entity") or "")
        if ent == node or ent.startswith(f"{node}:") or f":{node}:" in ent:
            return True
    return False


def router(call: Callable[..., Awaitable[Any]], reader: Callable[..., Any]) -> APIRouter:
    r = APIRouter()
    cache: dict[str, Any] = {"t": 0.0, "data": None}
    lock = asyncio.Lock()

    async def soft(name: str, *a: Any, **kw: Any) -> dict:
        """A service read that degrades to ``available: false`` instead of failing the page."""
        try:
            out = await call(name, *a, **kw)
        except HTTPException as e:
            return {"available": False, "reason": _scrub(e.detail)}
        except Exception as e:  # noqa: BLE001
            return {"available": False, "reason": type(e).__name__}
        return out if isinstance(out, dict) else {"available": False, "reason": "unexpected payload"}

    async def build_topology() -> dict:
        from shimpaku.actions import fixture

        inv, topo, routing, health, pc, incs, acts = await asyncio.gather(
            soft("inventory_list"), soft("topo_get_neighbors"), soft("routing_get_health"),
            soft("device_get_health", window_min=15), soft("port_get_config", fixture.DEVICE, fixture.IFNAME),
            soft("incident_list"), soft("action_list"))
        nodes = nodes_of(inv)
        lldp = [x for x in topo.get("lldp") or [] if isinstance(x, dict)]
        rmap = {d.get("device"): d for d in routing.get("devices") or [] if isinstance(d, dict)}
        hmap = {d.get("device"): d for d in health.get("devices") or [] if isinstance(d, dict)}
        ports = {k: (v.get("ports") or {}) for k, v in hmap.items()}
        incidents = view.incident_list(incs).get("incidents") or [] if incs.get("available", True) else []
        open_incs = [i for i in (incs.get("incidents") or []) if i.get("status") == "open"]
        actions = (view.action_list(acts).get("actions") or []) if acts.get("available", True) else []
        day = time.time() - 86400
        links = [link_state(lk, lldp, rmap, ports) for lk in intended_links(topo)]
        site_of = {n["name"]: n["site"] for n in nodes}
        for lk in links:
            lk["site"] = site_of.get(lk["a"]) or site_of.get(lk["b"])
        for n in nodes:
            name = n["name"]
            h = hmap.get(name) or {}
            rt = rmap.get(name)
            reach = None if rt is None else not rt.get("error")
            last = (h.get("last") or {}).get("status")
            n["reachable"] = reach
            n["state"] = ("down" if reach is False else "degraded" if last == "bad" else
                          "up" if reach or last == "good" else "unknown")
            n["health"] = {k: h.get(k) for k in ("cpu_pct", "mem_pct", "sampled_at", "minutes", "last", "classifiers")} if h else None
            n["routing"] = ({"ospf_full": sum(1 for x in rt.get("ospf") or [] if x.get("state") == "Full"),
                             "ospf": len(rt.get("ospf") or []),
                             "bfd_up": sum(1 for x in rt.get("bfd") or [] if x.get("state") == "Up"),
                             "bfd": len(rt.get("bfd") or []),
                             "bgp_established": sum(1 for x in rt.get("bgp") or [] if x.get("state") == "Established"),
                             "bgp": len(rt.get("bgp") or [])} if rt and not rt.get("error") else None)
            n["incidents"] = [i.get("incident_id") or i.get("id") for i in open_incs if _touches(name, i)]
            n["actions"] = [{k: a.get(k) for k in ("id", "status", "title", "created_at")}
                            for a in actions if a.get("device") == name and (a.get("created_at") or 0) >= day][:6]
            n["links"] = [lk["id"] for lk in links if name in (lk["a"], lk["b"])]
        fx = fixture_state(pc)
        sources = {"inventory": inv.get("reason") if inv.get("available") is False else "ok",
                   "lldp": topo.get("reason") if topo.get("available") is False else ("ok" if lldp else "none"),
                   "routing": routing.get("reason") if routing.get("available") is False else "ok",
                   "health": health.get("reason") if health.get("available") is False else "ok",
                   "fixture": fx["reason"] or "ok"}
        sites = sorted({n["site"] for n in nodes if n["site"]})
        return {"generated_at": time.time(), "sites": sites, "nodes": nodes, "links": links, "fixture": fx,
                "sources": sources, "incidents_open": len(open_incs), "all_incidents": len(incidents),
                **({"demo": True} if inv.get("demo") else {})}

    async def topology_cached() -> dict:
        async with lock:
            if cache["data"] is None or time.time() - cache["t"] > TOPO_TTL_S:
                cache["data"] = await build_topology()
                cache["t"] = time.time()
            return cache["data"]

    @r.get("/api/viz/topology")
    async def viz_topology(p=Depends(reader)) -> dict:
        """Intended links (lab TOML) with live state from LLDP, OSPF / BFD, stored port oper state;
        per-node health and the demo fixture trunk's VLAN 999 membership."""
        return await topology_cached()

    @r.get("/api/viz/overview")
    async def viz_overview(p=Depends(reader)) -> dict:
        topo = await topology_cached()
        sle_c, sle_f, incs, acts, aud = await asyncio.gather(
            soft("sle_get_summary", "campus", window_min=60), soft("sle_get_summary", "fabric", window_min=60),
            soft("incident_list"), soft("action_list"), soft("audit_verify"))
        sles = []
        for site, raw in (("campus", sle_c), ("fabric", sle_f)):
            v = view.sle_summary(raw)
            if not isinstance(v, dict) or v.get("available") is False:
                continue
            for s in v.get("sles") or []:
                sles.append({"site": site, "id": s.get("id"), "name": s.get("name"), "value_pct": s.get("value_pct"),
                             "n": s.get("n"), "good": s.get("good"), "bad": s.get("bad"),
                             "series": [{"t": x.get("t"), "value": x.get("value")} for x in s.get("series") or []]})
        inc_rows = view.incident_list(incs).get("incidents") or [] if incs.get("available", True) else []
        act_rows = view.action_list(acts).get("actions") or [] if acts.get("available", True) else []
        midnight = time.time() // 86400 * 86400  # 00:00 UTC
        today = [a for a in act_rows if (a.get("created_at") or 0) >= midnight]
        by_status: dict[str, int] = {}
        for a in today:
            by_status[a.get("status") or "?"] = by_status.get(a.get("status") or "?", 0) + 1
        feed = []
        for i in inc_rows[:20]:
            feed.append({"t": i.get("opened_at"), "kind": "incident", "text": i.get("title"), "status": i.get("status"),
                         "ref": f"/root-cause/{i.get('id')}", "site": i.get("site")})
            if i.get("closed_at"):
                feed.append({"t": i.get("closed_at"), "kind": "incident", "text": f"{i.get('id')} closed",
                             "status": "closed", "ref": f"/root-cause/{i.get('id')}", "site": i.get("site")})
        for a in act_rows[:30]:
            feed.append({"t": a.get("updated_at") or a.get("created_at"), "kind": "action", "text": a.get("title"),
                         "status": a.get("status"), "ref": f"/actions/{a.get('id')}", "device": a.get("device")})
        for raw in (sle_c, sle_f):
            for c in raw.get("changes") or []:
                if c.get("kind") == "commit":
                    feed.append({"t": c.get("t"), "kind": "commit", "text": c.get("text"), "status": None,
                                 "ref": None, "device": c.get("device")})
        feed = sorted((f for f in feed if f.get("t")), key=lambda f: -f["t"])[:14]
        nodes = topo["nodes"]
        known = [n for n in nodes if n.get("reachable") is not None]
        audit = None
        if aud.get("available", True) and "ok" in aud:
            audit = {"ok": bool(aud.get("ok")), "rows": aud.get("rows"), "first_bad": aud.get("first_bad")}
        return {
            "generated_at": time.time(),
            "devices": {"total": len(nodes), "reachable": sum(1 for n in known if n["reachable"]),
                        "known": len(known), "degraded": sum(1 for n in nodes if n["state"] == "degraded")},
            "links": {"total": len(topo["links"]),
                      **{s: sum(1 for lk in topo["links"] if lk["state"] == s) for s in ("up", "degraded", "down", "unknown")}},
            "sles": sles,
            "incidents": {"open": sum(1 for i in inc_rows if i.get("status") == "open"), "total": len(inc_rows),
                          "available": incs.get("available", True) is not False},
            "actions_today": {"since": midnight, "total": len(today), "by_status": by_status,
                              "blocked": by_status.get("blocked", 0)},
            "audit": audit,
            "fixture": topo["fixture"],
            "feed": feed,
            **({"demo": True} if topo.get("demo") else {}),
        }

    @r.get("/api/viz/devices/{name}")
    async def viz_device(name: str, window: int = 60, p=Depends(reader)) -> dict:
        topo = await topology_cached()
        node = next((n for n in topo["nodes"] if n["name"] == name), None)
        if node is None:
            raise HTTPException(404, "unknown device")
        window = max(15, min(int(window), 24 * 60))
        health, errs, hist, rt = await asyncio.gather(
            soft("device_get_health", name, window_min=window), soft("iface_get_errors", name),
            soft("iface_get_history", name, window_min=window), soft("routing_get_health", name))
        h = next((d for d in health.get("devices") or [] if d.get("device") == name), None)
        rtd = next((d for d in rt.get("devices") or [] if d.get("device") == name), None)
        links = [lk for lk in topo["links"] if name in (lk["a"], lk["b"])]
        return {
            "device": {k: node.get(k) for k in ("name", "site", "kind", "role", "version", "topology", "state", "reachable")},
            "window_min": window,
            "health": h if h else {"available": False, "reason": health.get("reason") or "no stored health for this device"},
            "interfaces": errs.get("interfaces") if isinstance(errs.get("interfaces"), list) else None,
            "interfaces_reason": None if isinstance(errs.get("interfaces"), list) else errs.get("reason"),
            "history": hist,
            "routing": ({k: rtd.get(k) for k in ("ospf", "bgp", "bfd")} if rtd and not rtd.get("error")
                        else {"available": False, "reason": _scrub((rtd or {}).get("error") or rt.get("reason") or "not read")}),
            "links": links,
            "incidents": node.get("incidents") or [],
            "actions": node.get("actions") or [],
            "fixture": topo["fixture"] if name == topo["fixture"]["device"] else None,
            **({"demo": True} if topo.get("demo") else {}),
        }

    r.topology = topology_cached  # type: ignore[attr-defined]  # shared with routes_hardware
    return r
