"""The hardware twin (``GET /api/hardware``): the seven vJunos guests on one KVM host, each as a
front panel whose ports, LEDs and numbers come from live state. Read-only.

``app.py`` mounts it with ``app.include_router(routes_hardware.router(call, reader, service, ...))``
next to ``routes_viz``, sharing its cached topology so no device is read twice.

Where each value comes from (nothing is filled in; absent is ``None`` with a reason):

- device state, reachability, CPU / memory: the ``/api/viz/topology`` build (NETCONF routing read,
  stored gNMI health);
- port oper state: the live NETCONF counters read when the port is in it, else the stored gNMI
  ``if.oper_up`` sample (``device_get_health.ports``), else the link evidence;
- bits/s per port: ``live_port_rates`` (Δoctets × 8 / Δt between the last two gNMI samples);
- packets, errors, drops, carrier transitions: ``iface_get_errors`` (live NETCONF, cached 30 s);
- Junos version, model, uptime: ``device_get_facts`` (live NETCONF, cached 15 min) where the
  service has it;
- vCPU / memory per guest: the lab topology files (``vcpu``, ``memory_mb``), i.e. *declared*;
- guest power state: the host sampler's ``virsh list`` (``app.state.hostmon``), matched to device
  names (``lab-<name>`` or ``<name>``); other domains on the host are never named.

Guest safety (visitors, testers): no management address, no configuration text, no interface
description, no host path, no other VM's name. The host block (CPU count, memory, load,
temperature) is returned to operators only; everyone else gets guest counts and states.
"""

from __future__ import annotations

import asyncio
import re
import time
import tomllib
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException

from shimpaku.api import routes_viz

TTL_S = 5.0
COUNTERS_TTL_S = 30.0
FACTS_TTL_S = 15 * 60.0
FACTS_FAIL_TTL_S = 120.0
SAMPLE_INTERVAL_S = 10
PORT_RE = re.compile(r"^(ge|xe|et|mge)-(\d{1,2})/(\d{1,2})/(\d{1,2})$")
COUNTER_KEYS = ("input_packets", "output_packets", "input_errors", "output_errors", "input_drops",
                "output_drops", "carrier_transitions", "mtu")

# Cited, static: what Juniper says each lab image is built on. Retrieved 2026-10-25; see
# docs/research/2026-10-25-hardware-twin.md. Wording is Juniper's, in quotes.
MODELS: dict[str, dict] = {
    "vjunos-switch": {
        "image": "vJunos-switch",
        "equivalent": "EX9214-class chassis switch",
        "relation": "Runs the same Junos OS as an EX9214-class switch: Juniper builds vJunos-switch "
                    "\"using EX9214 as a reference Juniper switch\".",
        "facts": ["\"a single Routing Engine and single Flexible PIC Concentrator (FPC)\"",
                  "\"Supports EVPN-VXLAN leaf functionality\", \"Edge-Routed Bridging (ERB)\", "
                  "\"EVPN LAG multihoming in EVPN-VXLAN (ESI-LAG)\"",
                  "\"Supports up to 96 switch interfaces\"; \"Gigabit Ethernet interfaces only\""],
        "caveats": ["\"up to 100 Mbps aggregated over all the interfaces\"",
                    "\"use only in labs and not in the production environment\"",
                    "Forwarding is a software plane in an x86 VM (the VFP), not the switch's ASICs: "
                    "same control plane and CLI, no line-rate or optics fidelity."],
        "min": {"vcpu": 4, "memory_mb": 5120},
        "sources": [
            {"label": "Juniper: vJunos-switch Overview",
             "url": "https://www.juniper.net/documentation/us/en/software/vjunos/vjunos-switch-kvm/topics/vjunos-switch-overview-understanding.html"},
            {"label": "Juniper: vJunos-switch Architecture",
             "url": "https://www.juniper.net/documentation/us/en/software/vjunos/vjunos-switch-kvm/topics/vjunos-switch-architecture-concept.html"},
            {"label": "Juniper: minimum requirements",
             "url": "https://www.juniper.net/documentation/us/en/software/vjunos/vjunos-switch-kvm/topics/vjunos-switch-kvm-hw-requirements.html"},
        ],
    },
    "vjunos-router": {
        "image": "vJunos-router",
        "equivalent": "MX Series router",
        "relation": "Runs the same Junos OS as the MX Series: Juniper builds vJunos-router \"using vMX "
                    "as a reference Juniper router\", with \"feature consistency with the physical MX "
                    "Series platforms\".",
        "facts": ["\"a single Routing Engine and single Flexible PIC Concentrator (FPC)\"",
                  "\"Feature consistency with the physical MX Series platforms\""],
        "caveats": ["\"up to 100 Mbps aggregated over all the interfaces\"",
                    "\"use only in labs and not in the production environment\"",
                    "Forwarding is a software plane in an x86 VM (the VFP), not an MX line card: "
                    "same control plane and CLI, no line-rate fidelity."],
        "min": {"vcpu": 4, "memory_mb": 5120},
        "sources": [
            {"label": "Juniper: vJunos-router Overview",
             "url": "https://www.juniper.net/documentation/us/en/software/vjunos-router/vjunos-router-kvm/topics/vjunos-router-overview-understanding.html"},
            {"label": "Juniper: minimum requirements",
             "url": "https://www.juniper.net/documentation/us/en/software/vjunos-router/vjunos-router-kvm/topics/vjunos-router-kvm-hw-requirements.html"},
        ],
    },
}


def model_key(kind: str | None) -> str | None:
    k = (kind or "").lower().replace("_", "-")
    if "router" in k:
        return "vjunos-router"
    if "switch" in k:
        return "vjunos-switch"
    return None


def port_sort(ifname: str) -> tuple:
    m = PORT_RE.match(ifname)
    return (int(m.group(2)), int(m.group(3)), int(m.group(4))) if m else (99, 99, 99)


def declared_resources() -> dict[str, dict]:
    """``vcpu`` / ``memory_mb`` per node from the lab topology files (never the management address)."""
    from shimpaku.config import settings

    out: dict[str, dict] = {}
    for p in settings.topology_paths():
        try:
            data = tomllib.loads(p.read_text())
        except (OSError, ValueError):
            continue
        for n in data.get("nodes", []):
            if n.get("name"):
                out[n["name"]] = {"vcpu": n.get("vcpu"), "memory_mb": n.get("memory_mb")}
    return out


def guest_states(vms: dict | None, names: list[str]) -> dict:
    """Lab guests only: ``{name: state}`` for domains named ``<name>`` or ``lab-<name>``."""
    if not isinstance(vms, dict):
        return {"states": {}, "sampled_at": None, "reason": "the host sampler is not running in this process"}
    by = {str(v.get("name")): v.get("state") for v in vms.get("vms") or [] if isinstance(v, dict)}
    states = {}
    for n in names:
        st = by.get(n) or by.get(f"lab-{n}")
        if st is not None:
            states[n] = st
    reason = None
    if vms.get("error"):
        reason = routes_viz._scrub(vms["error"])
    elif not states:
        reason = "no domain on the host matches a lab device name"
    return {"states": states, "sampled_at": vms.get("t"), "reason": reason}


def build_ports(name: str, links: list[dict], gnmi: dict, rates: dict, counters: list[dict] | None,
                fixture: dict | None) -> list[dict]:
    """One row per physical port known from any source, in slot order."""
    cmap = {c.get("ifname"): c for c in counters or [] if isinstance(c, dict) and c.get("ifname")}
    wired: dict[str, dict] = {}
    for lk in links:
        for side, other in (("a", "b"), ("b", "a")):
            if lk.get(side) == name and lk.get(f"{side}_if"):
                wired[lk[f"{side}_if"]] = {"peer": lk.get(other), "peer_if": lk.get(f"{other}_if"),
                                           "role": lk.get("role"), "link": lk.get("id"),
                                           "link_state": lk.get("state"), "lldp": (lk.get("evidence") or {}).get("lldp"),
                                           "link_oper": (lk.get("evidence") or {}).get(f"oper_{side}")}
    fx_if = fixture.get("ifname") if fixture and fixture.get("device") == name else None
    names = {n for n in (*wired, *gnmi, *rates, *cmap, *([fx_if] if fx_if else [])) if n and PORT_RE.match(n)}
    out = []
    for ifn in sorted(names, key=port_sort):
        w = wired.get(ifn) or {}
        c = cmap.get(ifn)
        r = rates.get(ifn) or {}
        if c and c.get("oper"):
            oper, src = c.get("oper"), "NETCONF (live)"
        elif gnmi.get(ifn):
            oper, src = gnmi.get(ifn), "gNMI (stored, ≤ 10 min)"
        elif ifn == fx_if and fixture.get("oper"):
            oper, src = fixture.get("oper"), "NETCONF (live)"
        elif w.get("link_oper"):
            oper, src = w["link_oper"], "gNMI (stored)"
        else:
            oper, src = None, None
        row: dict[str, Any] = {
            "ifname": ifn, "index": port_sort(ifn)[2], "wired": bool(w), "peer": w.get("peer"),
            "peer_if": w.get("peer_if"), "role": w.get("role") or ("demo trunk" if ifn == fx_if else None),
            "link": w.get("link"), "link_state": w.get("link_state"), "lldp": w.get("lldp"),
            "oper": oper, "oper_source": src, "admin": (c or {}).get("admin") or (fixture.get("admin") if ifn == fx_if else None),
            "in_bps": r.get("in_bps"), "out_bps": r.get("out_bps"), "rate_t": r.get("t"),
            "counters": {k: c.get(k) for k in COUNTER_KEYS} if c else None,
        }
        if ifn == fx_if:
            row["fixture"] = {"vlan": fixture.get("vlan"), "present": fixture.get("present")}
        out.append(row)
    return out


def router(call: Callable[..., Awaitable[Any]], reader: Callable[..., Any], service: Any = None, *,
           topology: Callable[[], Awaitable[dict]] | None = None,
           hostmon: Callable[[], Any] | None = None) -> APIRouter:
    r = APIRouter()
    demo = bool(getattr(service, "DEMO", False))
    if topology is None:  # standalone (tests): a private topology cache over the same service
        topology = routes_viz.router(call, reader).topology
    cache: dict[str, tuple[float, Any]] = {}
    locks: dict[str, asyncio.Lock] = {}

    async def cached(key: str, ttl: float, fn: Callable[[], Awaitable[Any]], fail_ttl: float | None = None) -> Any:
        lock = locks.setdefault(key, asyncio.Lock())
        async with lock:
            hit = cache.get(key)
            if hit is not None and time.time() < hit[0]:
                return hit[1]
            val = await fn()
            bad = isinstance(val, dict) and val.get("available") is False
            cache[key] = (time.time() + (fail_ttl if bad and fail_ttl is not None else ttl), val)
            return val

    async def soft(name: str, *a: Any, **kw: Any) -> dict:
        if service is not None and not hasattr(service, name):
            if demo:
                from shimpaku.api import demo_hardware
                if hasattr(demo_hardware, name):
                    return await getattr(demo_hardware, name)(*a, **kw)
            return {"available": False, "reason": "not collected by this server"}
        try:
            out = await call(name, *a, **kw)
        except HTTPException as e:
            return {"available": False, "reason": routes_viz._scrub(e.detail)}
        except Exception as e:  # noqa: BLE001
            return {"available": False, "reason": type(e).__name__}
        return out if isinstance(out, dict) else {"available": False, "reason": "unexpected payload"}

    async def rates() -> dict:
        try:
            if service is not None and hasattr(service, "live_port_rates"):
                out = await call("live_port_rates", at=None)
            elif demo:
                from shimpaku.api import demo_live
                out = await demo_live.live_port_rates(at=None)
            else:
                from shimpaku import service as svc
                out = await svc._call_provider("live_port_rates", at=None)
        except Exception as e:  # noqa: BLE001
            return {"available": False, "reason": type(e).__name__}
        if not isinstance(out, dict) or isinstance(out.get("error"), dict):
            return {"available": False, "reason": "provider failed"}
        return out

    def declared() -> dict[str, dict]:
        if demo:
            from shimpaku.api import demo_hardware
            return demo_hardware.declared()
        try:
            return declared_resources()
        except Exception:  # noqa: BLE001
            return {}

    def host_view(operator: bool, names: list[str]) -> dict:
        if demo:
            from shimpaku.api import demo_hardware
            vms, latest = demo_hardware.vms(), demo_hardware.host_latest()
        else:
            store = hostmon() if hostmon else None
            vms = getattr(store, "vms", None) if store is not None else None
            samples = store.samples(since_s=120) if store is not None and hasattr(store, "samples") else []
            latest = samples[-1] if samples else None
        g = guest_states(vms, names)
        counts: dict[str, int] = {}
        for st in g["states"].values():
            counts[st] = counts.get(st, 0) + 1
        out = {"guests": g["states"], "guest_counts": counts, "guests_sampled_at": g["sampled_at"],
               "guests_reason": g["reason"], "host": None}
        if operator and isinstance(latest, dict):
            out["host"] = {k: latest.get(k) for k in ("t", "ncpu", "mem_total_mb", "mem_avail_mb", "load",
                                                      "temp_c", "temp_crit_c")}
        return out

    async def device_row(n: dict, topo: dict, gnmi_all: dict, rate_ports: dict, res: dict) -> dict:
        name = n["name"]
        errs, facts = await asyncio.gather(
            cached(f"c:{name}", COUNTERS_TTL_S, lambda: soft("iface_get_errors", name)),
            cached(f"f:{name}", FACTS_TTL_S, lambda: soft("device_get_facts", name), fail_ttl=FACTS_FAIL_TTL_S))
        counters = errs.get("interfaces") if isinstance(errs.get("interfaces"), list) else None
        links = [lk for lk in topo.get("links") or [] if name in (lk.get("a"), lk.get("b"))]
        gnmi = {k: v for k, v in (gnmi_all.get(name) or {}).items() if PORT_RE.match(k)}
        ports = build_ports(name, links, gnmi, rate_ports.get(name) or {}, counters, topo.get("fixture"))
        h = n.get("health") or {}
        mk = model_key(n.get("kind"))
        fok = facts.get("available") is not False
        version = (facts.get("version") if fok else None) or n.get("version")
        return {
            "name": name, "site": n.get("site"), "role": n.get("role"), "kind": n.get("kind"), "model": mk,
            "state": n.get("state"), "reachable": n.get("reachable"),
            "cpu_pct": h.get("cpu_pct"), "mem_pct": h.get("mem_pct"), "sampled_at": h.get("sampled_at"),
            "minutes": h.get("minutes"), "last": h.get("last"),
            "version": version, "uptime_s": facts.get("uptime_s") if fok else None,
            "booted_at": facts.get("booted_at") if fok else None,
            "facts_reason": None if version else (facts.get("reason") if not fok else "the device did not report it"),
            "declared": res.get(name),
            "ports": ports,
            "counters_reason": None if counters is not None else errs.get("reason") or "not read",
            "routing": n.get("routing"), "incidents": n.get("incidents") or [],
        }

    async def build(operator: bool) -> dict:
        topo, health, rt = await asyncio.gather(topology(), soft("device_get_health", window_min=15), rates())
        gnmi_all = {d.get("device"): d.get("ports") or {} for d in health.get("devices") or [] if isinstance(d, dict)}
        rate_ports = rt.get("ports") or {} if rt.get("available") is not False else {}
        res = declared()
        nodes = topo.get("nodes") or []
        devices = await asyncio.gather(*(device_row(n, topo, gnmi_all, rate_ports, res) for n in nodes))
        names = [n["name"] for n in nodes]
        hv = host_view(operator, names)
        dec = [res.get(n) or {} for n in names]
        last = max([d["sampled_at"] or 0 for d in devices] + [p.get("rate_t") or 0 for d in devices for p in d["ports"]] or [0]) or None
        return {
            "generated_at": time.time(),
            "telemetry": {"interval_s": SAMPLE_INTERVAL_S, "transport": "gNMI dial-in + NETCONF reads",
                          "last_sample_t": last,
                          "rates": None if rt.get("available") is not False else rt.get("reason"),
                          "rates_source": rt.get("source")},
            "hypervisor": {
                "kind": "libvirt / KVM", "hosts": 1, "devices": len(names),
                "declared_vcpu": sum(d.get("vcpu") or 0 for d in dec) or None,
                "declared_memory_mb": sum(d.get("memory_mb") or 0 for d in dec) or None,
                "declared_source": "lab topology files (vcpu, memory_mb per node)" if any(dec) else None,
                **hv,
            },
            "models": {k: v for k, v in MODELS.items() if any(d["model"] == k for d in devices)},
            "devices": devices,
            **({"demo": True} if topo.get("demo") or demo else {}),
        }

    @r.get("/api/hardware")
    async def hardware(p=Depends(reader)) -> dict:
        """The seven guests as front panels: ports with live oper state and bits/s, counters,
        CPU / memory, version and uptime where collected; the hypervisor they run on."""
        op = bool(getattr(p, "is_operator", False))
        if getattr(service, "REPLAY", False):
            # the replay has no devices to read: serve the visitor view recorded with the snapshot
            hw = service.hardware_snapshot() if hasattr(service, "hardware_snapshot") else None
            if hw:
                return {**hw, "replay": True}
        return await cached(f"hw:{int(op)}", TTL_S, lambda: build(op))

    return r
