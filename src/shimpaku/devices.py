"""PyEZ connection pool and the read RPC wrappers behind the T0 tools (CONTRACT: owner B-mcp).

Reads only. Every write goes through `shimpaku.actions.executor`, which opens its own sessions
(so a broken pooled session can never carry a half-made change).

R2 §6: one NETCONF connect costs 0.8–1.2 s and an RPC 120–300 ms, so sessions are pooled per
device and re-opened on failure. PyEZ is synchronous; callers in asyncio wrap these in
`asyncio.to_thread` (see `shimpaku.service`).
"""

from __future__ import annotations

import re
import threading
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from shimpaku.config import settings

# A physical data port name. Anything else (ranges, wildcards, units, mgmt ports) is refused by
# the action guardrails and by the per-port read tools.
PORT_RE = re.compile(r"^(ge|xe|et|mge)-\d{1,2}/\d{1,2}/\d{1,2}$")
DEVICE_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
SITE_BY_TOPOLOGY = {"day-02-switching": "campus", "day-06-datacenter": "fabric"}


@dataclass(frozen=True)
class Node:
    name: str
    site: str
    kind: str
    mgmt_ip: str
    topology: str


@dataclass(frozen=True)
class LinkEnd:
    node: str
    ifname: str
    link: str
    peer: str
    role: str  # fabric | trunk | routed


@dataclass
class Inventory:
    nodes: dict[str, Node] = field(default_factory=dict)
    ports: dict[tuple[str, str], LinkEnd] = field(default_factory=dict)

    def port_role(self, device: str, ifname: str) -> str | None:
        end = self.ports.get((device, ifname))
        return end.role if end else None


def _role_for_link(lab: str, link: dict) -> str:
    comment = (link.get("comment") or "") + " " + (link.get("name") or "")
    if lab == "day-06-datacenter":
        return "fabric"
    if "subnet" not in link:  # no L3 address on the link: a trunk (day-02 r1–sw1)
        return "trunk"
    return "routed" if "trunk" not in comment else "trunk"


def load_inventory(paths: list[Path] | None = None) -> Inventory:
    """Nodes and wired ports from the lab TOMLs.

    Port numbering follows the generator: each node's links take ge-0/0/0, ge-0/0/1, … in the
    order the links appear in the file (verified live: leaf2 ge-0/0/0 = leaf2-spine1, r1
    ge-0/0/1 = r1-sw1 trunk). This is the *intended* role; the guardrails also read the live
    role and keep the more protective of the two.
    """
    inv = Inventory()
    for p in paths if paths is not None else settings.topology_paths():
        if not p.exists():
            continue
        data = tomllib.loads(p.read_text())
        lab = data.get("lab", {}).get("name", p.stem)
        site = SITE_BY_TOPOLOGY.get(lab, lab)
        for n in data.get("nodes", []):
            inv.nodes[n["name"]] = Node(n["name"], site, n.get("kind", "?"), n["mgmt_ip"], lab)
        next_port: dict[str, int] = {}
        for link in data.get("links", []):
            a, b = link["nodes"]
            role = _role_for_link(lab, link)
            for me, peer in ((a, b), (b, a)):
                idx = next_port.get(me, 0)
                next_port[me] = idx + 1
                ifn = f"ge-0/0/{idx}"
                inv.ports[(me, ifn)] = LinkEnd(me, ifn, link.get("name", f"{a}-{b}"), peer, role)
    return inv


_inventory: Inventory | None = None


def inventory() -> Inventory:
    global _inventory
    if _inventory is None:
        _inventory = load_inventory()
    return _inventory


class UnknownDevice(KeyError):
    pass


def node(name: str) -> Node:
    if not isinstance(name, str) or not DEVICE_RE.match(name) or name not in inventory().nodes:
        raise UnknownDevice(f"unknown device {name!r}")
    return inventory().nodes[name]


def open_device(name: str, timeout: int = 60):
    """A fresh PyEZ session (not pooled). The executor uses this for writes and health checks."""
    from jnpr.junos import Device

    n = node(name)
    dev = Device(host=n.mgmt_ip, user=settings.device_user, password=settings.device_password,
                 port=830, gather_facts=False, conn_open_timeout=20)
    dev.open()
    dev.timeout = timeout
    return dev


class Pool:
    """One pooled read session per device, serialised by a per-device lock."""

    def __init__(self, opener: Callable[[str], Any] = open_device, idle_s: float = 300):
        self._opener = opener
        self._idle_s = idle_s
        self._devs: dict[str, tuple[Any, float]] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock(self, name: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(name, threading.Lock())

    def run(self, name: str, fn: Callable[[Any], Any]) -> Any:
        node(name)  # validates the name before any connect
        with self._lock(name):
            for attempt in (1, 2):
                dev, last = self._devs.get(name, (None, 0.0))
                if dev is None or not getattr(dev, "connected", False) or time.monotonic() - last > self._idle_s:
                    if dev is not None:
                        try:
                            dev.close()
                        except Exception:  # noqa: BLE001
                            pass
                    dev = self._opener(name)
                try:
                    out = fn(dev)
                    self._devs[name] = (dev, time.monotonic())
                    return out
                except Exception:
                    self._devs.pop(name, None)
                    try:
                        dev.close()
                    except Exception:  # noqa: BLE001
                        pass
                    if attempt == 2:
                        raise
        raise RuntimeError("unreachable")

    def close_all(self) -> None:
        with self._guard:
            for dev, _ in self._devs.values():
                try:
                    dev.close()
                except Exception:  # noqa: BLE001
                    pass
            self._devs.clear()


pool = Pool()


# ---------------------------------------------------------------- XML helpers (pure, testable)

def _t(el, path: str, default: str | None = None) -> str | None:
    v = el.findtext(path)
    return v.strip() if v is not None else default


def _int(el, path: str) -> int | None:
    v = _t(el, path)
    try:
        return int(v) if v not in (None, "") else None
    except ValueError:
        return None


def parse_interface_errors(xml) -> list[dict]:
    out = []
    for p in xml.iter("physical-interface"):
        name = _t(p, "name")
        if not name or not PORT_RE.match(name):
            continue
        out.append({
            "ifname": name,
            "admin": _t(p, "admin-status"),
            "oper": _t(p, "oper-status"),
            "description": _t(p, "description"),
            "input_errors": _int(p, "input-error-list/input-errors"),
            "input_drops": _int(p, "input-error-list/input-drops"),
            "framing_errors": _int(p, "input-error-list/framing-errors"),
            "fcs_errors": _int(p, "ethernet-mac-statistics/input-fcs-errors"),
            "output_errors": _int(p, "output-error-list/output-errors"),
            "output_drops": _int(p, "output-error-list/output-drops"),
            "carrier_transitions": _int(p, "output-error-list/carrier-transitions"),
            "input_packets": _int(p, "traffic-statistics/input-packets"),
            "output_packets": _int(p, "traffic-statistics/output-packets"),
            "mtu": _t(p, "mtu"),
        })
    return out


def parse_lldp(xml) -> list[dict]:
    out = []
    for n in xml.iter("lldp-neighbor-information"):
        out.append({
            "local_if": _t(n, "lldp-local-port-id") or _t(n, "lldp-local-interface"),
            "remote_system": _t(n, "lldp-remote-system-name"),
            "remote_port": _t(n, "lldp-remote-port-description") or _t(n, "lldp-remote-port-id"),
            "remote_chassis": _t(n, "lldp-remote-chassis-id"),
        })
    return out


def parse_ospf(xml) -> list[dict]:
    return [{"address": _t(n, "neighbor-address"), "interface": _t(n, "interface-name"),
             "state": _t(n, "ospf-neighbor-state"), "neighbor_id": _t(n, "neighbor-id")}
            for n in xml.iter("ospf-neighbor")]


def parse_bgp(xml) -> list[dict]:
    return [{"peer": (_t(p, "peer-address") or "").split("+")[0], "state": _t(p, "peer-state"),
             "peer_as": _t(p, "peer-as"), "flaps": _int(p, "flap-count")}
            for p in xml.iter("bgp-peer")]


def parse_bfd(xml) -> list[dict]:
    return [{"neighbor": _t(s, "session-neighbor"), "state": _t(s, "session-state"),
             "interface": _t(s, "session-interface")}
            for s in xml.iter("bfd-session")]


def parse_commits(xml, n: int) -> list[dict]:
    out = []
    for c in list(xml.iter("commit-history"))[:n]:
        dt = c.find("date-time")
        out.append({
            "index": _int(c, "sequence-number"),
            "user": _t(c, "user"),
            "client": _t(c, "client"),
            "time": dt.text.strip() if dt is not None and dt.text else None,
            "epoch": int(dt.get("seconds")) if dt is not None and dt.get("seconds") else None,
            "log": _t(c, "log"),
            "confirmed_pending": "pending" in (_t(c, "commit-confirmed") or "")
            or (out == [] and "rollback in" in (_t(c, "comment") or "")),
            "comment": _t(c, "comment"),
        })
    return out


# ---------------------------------------------------------------- live port facts

def port_facts(dev, ifname: str) -> dict:
    """Effective config (inheritance expanded: apply-groups, interface-range) and live state.

    `display inheritance` matters for smuggling: a port with no config of its own can still be
    a routed or trunk port through a wildcard group or an interface-range member list.
    """
    own_set = dev.cli(f"show configuration interfaces {ifname} | display set", warning=False) or ""
    inh_set = dev.cli(f"show configuration interfaces {ifname} | display inheritance | display set",
                      warning=False) or ""
    all_if = dev.cli("show configuration interfaces | display set", warning=False) or ""
    ranges = [l for l in all_if.splitlines() if l.startswith("set interfaces interface-range ")]
    range_hits = [l for l in ranges if l.strip().endswith(f" member {ifname}") or _range_covers(l, ifname)]
    # apply-groups with a wildcard interface name (<ge-*>) reach the port through inheritance;
    # `display inheritance` above already folds them in. Record the group lines too, for the reader.
    vlan_set = dev.cli("show configuration vlans | display set", warning=False) or ""
    vlan_hits = [l for l in vlan_set.splitlines() if f" interface {ifname}" in l]
    terse = dev.rpc.get_interface_information(interface_name=ifname, terse=True)
    lldp = parse_lldp(dev.rpc.get_lldp_neighbors_information())
    text = inh_set + "\n" + "\n".join(range_hits) + "\n" + "\n".join(vlan_hits)
    family_inet = bool(re.search(r"family (inet|inet6|iso|mpls)\b", text))
    trunk = "interface-mode trunk" in text or "port-mode trunk" in text
    eth_sw = "family ethernet-switching" in text or bool(vlan_hits)
    neighbor = next((n for n in lldp if n["local_if"] == ifname), None)
    live_role = ("fabric" if neighbor and neighbor.get("remote_system") in inventory().nodes
                 else "routed" if family_inet else "trunk" if trunk
                 else "access" if eth_sw else "unused")
    has_cfg = bool(own_set.strip() or range_hits or vlan_hits or inh_set.strip())
    return {
        "ifname": ifname,
        "admin": _t(terse, ".//physical-interface/admin-status"),
        "oper": _t(terse, ".//physical-interface/oper-status"),
        "config_own": own_set.strip(),
        "config_inherited": inh_set.strip(),
        "interface_range_lines": range_hits,
        "vlan_lines": vlan_hits,
        "lldp_neighbor": neighbor,
        "live_role": live_role,
        "configured": has_cfg,
    }


def _range_covers(line: str, ifname: str) -> bool:
    """`set interfaces interface-range R member-range ge-0/0/2 to ge-0/0/9` covers ge-0/0/5."""
    m = re.search(r"member-range (\S+) to (\S+)", line)
    if not m:
        m2 = re.search(r" member (\S+)$", line.strip())
        if m2 and any(ch in m2.group(1) for ch in "*[]"):
            pat, inside = "^", False
            for ch in m2.group(1):
                if ch == "[":
                    inside, pat = True, pat + "["
                elif ch == "]":
                    inside, pat = False, pat + "]"
                elif ch == "*" and not inside:
                    pat += ".*"
                else:
                    pat += ch if inside and (ch.isalnum() or ch in "-,") else re.escape(ch)
            pat = pat.replace(",", "") + "$"
            try:
                return re.match(pat, ifname) is not None
            except re.error:
                return True  # unparseable member pattern: assume it covers us (fail closed)
        return False

    def key(s: str):
        m3 = re.match(r"^([a-z]+)-(\d+)/(\d+)/(\d+)$", s)
        return (m3.group(1), int(m3.group(2)), int(m3.group(3)), int(m3.group(4))) if m3 else None

    lo, hi, me = key(m.group(1)), key(m.group(2)), key(ifname)
    if not (lo and hi and me) or lo[0] != me[0]:
        return False
    return lo[1:] <= me[1:] <= hi[1:]


def health_snapshot(dev) -> dict:
    """Protocol health used before/after a change: counts must not drop."""
    ospf = parse_ospf(dev.rpc.get_ospf_neighbor_information())
    try:
        bgp = parse_bgp(dev.rpc.get_bgp_summary_information())
    except Exception:  # noqa: BLE001  (no BGP configured)
        bgp = []
    try:
        bfd = parse_bfd(dev.rpc.get_bfd_session_information())
    except Exception:  # noqa: BLE001
        bfd = []
    return {
        "ospf_full": sum(1 for n in ospf if n["state"] == "Full"),
        "bgp_established": sum(1 for p in bgp if p["state"] == "Established"),
        "bfd_up": sum(1 for s in bfd if s["state"] == "Up"),
    }


# ---------------------------------------------------------------- read wrappers (sync)

def iface_errors(device: str, ifname: str | None = None) -> dict:
    if ifname is not None and not PORT_RE.match(ifname):
        raise ValueError(f"not a physical port name: {ifname!r}")

    def f(dev):
        kw = {"extensive": True}
        if ifname:
            kw["interface_name"] = ifname
        else:
            kw["interface_name"] = "[gx]e-*"
        return parse_interface_errors(dev.rpc.get_interface_information(**kw))

    rows = pool.run(device, f)
    if ifname is None:  # keep the answer small: wired ports plus any port with errors
        rows = [r for r in rows if r["oper"] == "up" or any((r.get(k) or 0) > 0 for k in
                ("input_errors", "output_errors", "fcs_errors", "input_drops", "output_drops"))]
    return {"device": device, "interfaces": rows}


def port_config(device: str, ifname: str) -> dict:
    if not PORT_RE.match(ifname or ""):
        raise ValueError(f"not a physical port name: {ifname!r}")
    facts = pool.run(device, lambda dev: port_facts(dev, ifname))
    facts["device"] = device
    facts["topology_role"] = inventory().port_role(device, ifname) or "unused"
    return facts


def lldp_neighbors(device: str | None = None) -> dict:
    names = [device] if device else sorted(inventory().nodes)
    out = []
    for name in names:
        try:
            for n in pool.run(name, lambda dev: parse_lldp(dev.rpc.get_lldp_neighbors_information())):
                out.append({"device": name, **n})
        except Exception as e:  # noqa: BLE001
            out.append({"device": name, "error": type(e).__name__})
    intended = [{"device": e.node, "ifname": e.ifname, "peer": e.peer, "link": e.link, "role": e.role}
                for e in inventory().ports.values() if device in (None, e.node)]
    return {"lldp": out, "intended": intended}


def routing_health(device: str | None = None) -> dict:
    names = [device] if device else sorted(inventory().nodes)
    out = []
    for name in names:
        def f(dev):
            ospf = parse_ospf(dev.rpc.get_ospf_neighbor_information())
            try:
                bgp = parse_bgp(dev.rpc.get_bgp_summary_information())
            except Exception:  # noqa: BLE001
                bgp = []
            try:
                bfd = parse_bfd(dev.rpc.get_bfd_session_information())
            except Exception:  # noqa: BLE001
                bfd = []
            return {"ospf": ospf, "bgp": bgp, "bfd": bfd}
        try:
            out.append({"device": name, **pool.run(name, f)})
        except Exception as e:  # noqa: BLE001
            out.append({"device": name, "error": f"{type(e).__name__}: unreachable"})
    return {"devices": out}


def commits(device: str, n: int = 10) -> dict:
    n = max(1, min(int(n), 50))
    rows = pool.run(device, lambda dev: parse_commits(dev.rpc.get_commit_information(), n))
    return {"device": device, "commits": rows}


def config_diff(device: str, rollback: int = 1) -> dict:
    rb = int(rollback)
    if not 1 <= rb <= 49:
        raise ValueError("rollback must be 1..49")
    text = pool.run(device, lambda dev: dev.cli(f"show system rollback {rb} compare 0", warning=False))
    return {"device": device, "rollback": rb, "diff": (text or "").strip()}


def raw_show(device: str, command: str) -> str:
    """Run an already-allowlisted `show` command (see shimpaku.mcp.junos_raw)."""
    return pool.run(device, lambda dev: dev.cli(command, warning=False)) or ""
