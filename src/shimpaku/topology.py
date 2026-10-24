"""Topology graph: entities and edges from the lab TOMLs, enriched with LLDP and interface addresses.

Sites: day-02 -> `campus`, day-06 -> `fabric` (CONTRACT.md). Interface names follow lab-gen's wiring: a node's
links take ge-0/0/0, ge-0/0/1, … in the order they appear in the TOML (verified on the lab: leaf2 ge-0/0/0 is
10.0.12.2 toward spine1, ge-0/0/1 toward spine2; sw1 ge-0/0/0 is the trunk to r1). Enrichment checks this
against device data: an LLDP neighbour wins over the TOML guess; an IPv4 address inside a link subnet confirms it.

Entity ids: see docs/build/store-api.md.
"""

from __future__ import annotations

import ipaddress
import re
import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shimpaku.config import settings

IFNAME = re.compile(r"^[a-z]{2,4}-\d+/\d+/\d+(\.\d+)?$")
SITE_BY_LAB = {"day-02": "campus", "day-06": "fabric"}


def site_for(lab_name: str, path: Path | None = None) -> str:
    for pfx, site in SITE_BY_LAB.items():
        if lab_name.startswith(pfx) or (path and path.name.startswith(pfx)):
            return site
    return lab_name


def ifname(ordinal: int) -> str:
    return f"ge-0/0/{ordinal}"


@dataclass
class Topology:
    entities: dict[str, dict] = field(default_factory=dict)
    edges: list[dict] = field(default_factory=list)

    def nodes(self) -> list[dict]:
        return [e for e in self.entities.values() if e["kind"] == "device"]

    def node(self, name: str) -> dict | None:
        e = self.entities.get(name)
        return e if e and e["kind"] == "device" else None

    def interfaces_of(self, node: str) -> set[str]:
        return {e["name"] for e in self.entities.values() if e["kind"] == "interface" and e["parent_id"] == node}

    def links(self) -> list[dict]:
        return [e for e in self.entities.values() if e["kind"] == "link"]

    def neighbors(self, node: str | None = None) -> list[dict]:
        out = []
        for e in self.edges:
            if e["relation"] != "connected_to":
                continue
            a_dev, a_if = e["src"].split(":", 1)
            b_dev, b_if = e["dst"].split(":", 1)
            if node is None or a_dev == node:
                out.append({"device": a_dev, "interface": a_if, "peer": b_dev, "peer_interface": b_if, **e["attrs"]})
        return out

    def to_dict(self) -> dict:
        return {"entities": list(self.entities.values()), "edges": self.edges}

    # internal
    def _add(self, entity_id: str, kind: str, name: str, parent: str | None, **attrs: Any) -> dict:
        e = self.entities.setdefault(entity_id, {"entity_id": entity_id, "kind": kind, "name": name,
                                                 "parent_id": parent, "attrs": {}})
        e["attrs"].update(attrs)
        if parent:
            edge = {"src": entity_id, "dst": parent, "relation": "member_of", "attrs": {}}
            if edge not in self.edges:
                self.edges.append(edge)
        return e


def load(paths: list[Path] | None = None) -> Topology:
    topo = Topology()
    for path in paths if paths is not None else settings.topology_paths():
        data = tomllib.loads(Path(path).read_text())
        lab = data.get("lab", {}).get("name", Path(path).stem)
        site = site_for(lab, Path(path))
        sid = f"site:{site}"
        topo._add(sid, "site", site, None, lab=lab, description=data.get("lab", {}).get("description"))
        for n in data.get("nodes", []):
            topo._add(n["name"], "device", n["name"], sid, site=site, model=n.get("kind"), mgmt_ip=n.get("mgmt_ip"),
                      role=_role(n["name"], n.get("kind", "")), vcpu=n.get("vcpu"), memory_mb=n.get("memory_mb"))
        used: dict[str, int] = {}
        for ln in data.get("links", []):
            a, b = ln["nodes"]
            ia, ib = ifname(used.get(a, 0)), ifname(used.get(b, 0))
            used[a], used[b] = used.get(a, 0) + 1, used.get(b, 0) + 1
            subnet = ln.get("subnet")
            lid = f"link:{ln['name']}"
            topo._add(lid, "link", ln["name"], sid, site=site, subnet=subnet, a=f"{a}:{ia}", b=f"{b}:{ib}")
            for dev, ifn in ((a, ia), (b, ib)):
                topo._add(f"{dev}:{ifn}", "interface", ifn, dev, link=ln["name"], source="toml")
                topo.edges.append({"src": lid, "dst": f"{dev}:{ifn}", "relation": "depends_on", "attrs": {}})
            attrs = {"link": ln["name"], "subnet": subnet, "source": "toml"}
            topo.edges.append({"src": f"{a}:{ia}", "dst": f"{b}:{ib}", "relation": "connected_to", "attrs": dict(attrs)})
            topo.edges.append({"src": f"{b}:{ib}", "dst": f"{a}:{ia}", "relation": "connected_to", "attrs": dict(attrs)})
    return topo


def _role(name: str, kind: str) -> str:
    for r in ("spine", "leaf"):
        if name.startswith(r):
            return r
    if name.startswith("sw") or kind.endswith("switch"):
        return "access"
    return "router"


def enrich(topo: Topology, lldp: dict[str, list[dict]] | None = None,
           addresses: dict[str, dict[str, list[str]]] | None = None) -> Topology:
    """Apply device evidence.

    lldp: node -> [{"interface": "ge-0/0/0", "peer": "spine1", "peer_interface": "ge-0/0/1"}]
    addresses: node -> {"ge-0/0/0": ["10.0.12.2/30"], ...}  (unit 0 addresses, ifd name)
    """
    lldp, addresses = lldp or {}, addresses or {}
    for node, ifs in addresses.items():
        for ifn, addrs in ifs.items():
            ent = topo.entities.get(f"{node}:{ifn}")
            if ent:
                ent["attrs"]["ipv4"] = addrs
    for e in topo.edges:
        if e["relation"] != "connected_to" or not e["attrs"].get("subnet"):
            continue
        net = ipaddress.ip_network(e["attrs"]["subnet"], strict=False)
        dev, ifn = e["src"].split(":", 1)
        got = addresses.get(dev, {}).get(ifn)
        if got is None:
            continue
        ok = any(ipaddress.ip_interface(a).ip in net for a in got)
        e["attrs"]["address_check"] = "ok" if ok else "mismatch"
        if not ok:  # find the interface that does carry the subnet
            for other, addrs in addresses.get(dev, {}).items():
                if any(ipaddress.ip_interface(a).ip in net for a in addrs):
                    e["attrs"]["address_suggests"] = other
    for node, nbrs in lldp.items():
        for nb in nbrs:
            src = f"{node}:{nb['interface']}"
            pif = nb.get("peer_interface")
            hit = next((e for e in topo.edges if e["relation"] == "connected_to" and e["src"] == src), None)
            if hit and hit["dst"].split(":", 1)[0] == nb["peer"] and (pif is None or hit["dst"] == f"{nb['peer']}:{pif}"):
                hit["attrs"]["lldp"] = "confirmed" if pif else "confirmed_device"  # port-id may be an ifIndex
                continue
            dst = f"{nb['peer']}:{pif or nb.get('port_id', '?')}"
            if hit:
                hit["attrs"]["lldp"] = f"contradicted:{dst}"
            if src not in topo.entities:
                topo._add(src, "interface", nb["interface"], node, source="lldp")
            if pif:
                topo.edges.append({"src": src, "dst": dst, "relation": "connected_to", "attrs": {"source": "lldp"}})
    return topo


def parse_gnmic_once(objs: list[dict]) -> tuple[dict[str, list[dict]], dict[str, dict[str, list[str]]]]:
    """Turn gnmic ONCE notifications (LLDP neighbours, ipv4 addresses) into enrich() inputs."""
    from shimpaku.collector.parse import keys

    lldp: dict[str, dict[tuple, dict]] = {}
    addrs: dict[str, dict[str, set]] = {}
    for o in objs:
        node = o.get("source")
        prefix = (o.get("prefix") or "").split(":", 1)[-1]
        for u in o.get("updates") or []:
            path = f"{prefix.rstrip('/')}/{u.get('Path', '')}"
            k = keys(path)
            val = next(iter((u.get("values") or {}).values()), None)
            if "/lldp/interfaces/interface" in path and "/neighbors/neighbor" in path:
                rec = lldp.setdefault(node, {}).setdefault((k.get("name"), k.get("id")), {"interface": k.get("name")})
                if path.endswith("state/system-name"):
                    rec["peer"] = str(val).split(".")[0]
                elif path.endswith("state/port-description") and IFNAME.match(str(val)):
                    rec["peer_interface"] = str(val)
                elif path.endswith("state/port-id"):
                    rec["port_id"] = str(val)
                    if IFNAME.match(str(val)):
                        rec["peer_interface"] = str(val)
            elif "/subinterfaces/subinterface" in path and "/ipv4/addresses/address" in path and path.endswith("prefix-length"):
                ifn = k.get("name")
                addrs.setdefault(node, {}).setdefault(ifn, set()).add(f"{k.get('ip')}/{int(val)}")
    return ({n: [r for r in d.values() if r.get("peer")] for n, d in lldp.items()},
            {n: {i: sorted(a) for i, a in d.items()} for n, d in addrs.items()})


def sync(store, topo: Topology | None = None) -> Topology:
    """Write entities and edges to the DuckDB store."""
    topo = topo or load()
    store.upsert_entities(topo.entities.values())
    store.replace_edges(topo.edges)
    return topo


if __name__ == "__main__":  # python -m shimpaku.topology
    print(json.dumps(load().to_dict(), indent=2))
