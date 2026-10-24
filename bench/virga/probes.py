"""Independent verification probes (never the agent, never the analytics) and expectation matching."""

from __future__ import annotations

import os
import re
import socket
import subprocess
import time
from pathlib import Path

PROBES: dict = {}


def probe(fn):
    PROBES[fn.__name__[2:]] = fn
    return fn


def _txt(el, path, default=""):
    v = el.findtext(path)
    return v.strip() if v is not None else default


@probe
def p_ospf(lab, node, ifname):
    """OSPF neighbour state on one interface ('absent' when there is none)."""
    r = lab.dev(node).rpc.get_ospf_neighbor_information()
    for n in r.findall(".//ospf-neighbor"):
        if _txt(n, "interface-name") == ifname:
            return _txt(n, "ospf-neighbor-state")
    return "absent"


@probe
def p_bfd(lab, node, ifname):
    r = lab.dev(node).rpc.get_bfd_session_information()
    for s in r.findall(".//bfd-session"):
        if _txt(s, "session-interface") == ifname:
            return _txt(s, "session-state")
    return "absent"


@probe
def p_nexthops(lab, node, prefix, table="inet.0"):
    """Number of next hops of the active route (ECMP width); 0 when the route is absent."""
    rt = lab.dev(node).rpc.get_route_information(destination=prefix, table=table)
    return len([h for h in rt.findall(".//rt-entry[current-active]/nh") if h.findtext("to")])


@probe
def p_bgp(lab, node, peer):
    from jnpr.junos.exception import RpcError

    try:
        r = lab.dev(node).rpc.get_bgp_neighbor_information(neighbor_address=peer)
    except RpcError:
        return "absent"
    return _txt(r, ".//peer-state") or "absent"


@probe
def p_ping(lab, node, target, instance=None, source=None, count=3, size=None):
    """Packet loss % of a rapid ping run ON a device."""
    kw = {"host": target, "count": str(count), "rapid": True}
    if instance:
        kw["routing_instance"] = instance
    if source:
        kw["source"] = source
    if size:
        kw["size"] = str(size)
    r = lab.dev(node).rpc.ping(**kw)
    loss = r.findtext(".//probe-results-summary/packet-loss")
    if loss is not None:
        return int(float(loss.strip()))
    return 0 if r.find(".//ping-success") is not None else 100


@probe
def p_iface(lab, node, ifname):
    """'admin/oper' of a physical interface, e.g. 'up/up', 'down/down'."""
    r = lab.dev(node).rpc.get_interface_information(interface_name=ifname, terse=True)
    phy = r.find(".//physical-interface")
    if phy is None:
        return "absent"
    return f"{_txt(phy, 'admin-status')}/{_txt(phy, 'oper-status')}"


@probe
def p_iface_flapped_ago(lab, node, ifname):
    """Seconds since the interface last changed state (Junos <interface-flapped seconds=...>)."""
    r = lab.dev(node).rpc.get_interface_information(interface_name=ifname, media=True)
    el = r.find(".//physical-interface/interface-flapped")
    if el is None:
        return -1
    for k, v in el.attrib.items():
        if k.endswith("seconds"):
            return int(v)
    m = re.search(r"\((?:(\d+)w)?(?:(\d+)d)?\s*(\d+):(\d+):(\d+) ago\)", el.text or "")
    if not m:
        return -1
    w, d, hh, mm, ss = (int(x or 0) for x in m.groups())
    return (((w * 7 + d) * 24 + hh) * 60 + mm) * 60 + ss, (el.text or "").strip()


@probe
def p_intent_missing(lab, node, intent):
    """How many lines of the intended config (generator output) are absent from the device."""
    want = [ln.strip() for ln in Path(os.path.expanduser(intent)).read_text().splitlines() if ln.strip()]
    have = set(lab.config_lines(node))
    missing = [ln for ln in want if ln not in have]
    return len(missing), missing


@probe
def p_ping_counter_delta(lab, node, target, instance, counter_node, filter, counter, count=3):
    """Ping from one device, report how many packets a firewall counter on another device saw."""
    def read():
        r = lab.dev(counter_node).rpc.get_firewall_filter_information(filtername=filter)
        for c in r.findall(".//counter"):
            if _txt(c, "counter-name") == counter:
                return int(_txt(c, "packet-count", "0"))
        return -1
    before = read()
    loss = p_ping(lab, node, target, instance=instance, count=count)
    after = read()
    return after - before, {"ping_loss_pct": loss, "counter_before": before, "counter_after": after}


@probe
def p_tcp(lab, host, port, timeout=4):
    """TCP connect from the runner host: open | refused | timeout | unreachable."""
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((lab.ip(host), int(port)))
        return "open"
    except ConnectionRefusedError:
        return "refused"
    except (socket.timeout, TimeoutError):
        return "timeout"
    except OSError:
        return "unreachable"
    finally:
        s.close()


@probe
def p_host_ping(lab, host, count=2):
    """ICMP from the runner host (no privileges needed): loss %."""
    r = subprocess.run(["ping", "-n", "-q", "-c", str(count), "-W", "1", lab.ip(host)], capture_output=True, text=True)
    m = re.search(r"([\d.]+)% packet loss", r.stdout)
    return int(float(m.group(1))) if m else 100


@probe
def p_pending_commit(lab, node):
    return lab.pending(node)


@probe
def p_top_commit(lab, node):
    r = lab.dev(node).rpc.get_commit_information()
    top = r.find(".//commit-history")
    return f"{_txt(top, 'user')}|{_txt(top, 'log')}|{_txt(top, 'date-time')}" if top is not None else ""


@probe
def p_clock_offset(lab, node):
    """Device clock minus runner clock, seconds (NTP-skew scenarios)."""
    r = lab.dev(node).rpc.get_system_uptime_information()
    el = r.find(".//current-time/date-time")
    seconds = next((int(v) for k, v in el.attrib.items() if k.endswith("seconds")), 0)
    return round(seconds - time.time())


@probe
def p_sh(lab, cmd, regex=None, on=None, timeout=30):
    """Shell probe for Linux targets (aiops-farm): exit code, or the first regex group of stdout."""
    argv = ["bash", "-c", cmd] if not on else ["ssh", "-o", "BatchMode=yes", on, cmd]
    r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if regex:
        m = re.search(regex, r.stdout + r.stderr, re.M)
        return (m.group(1) if m.groups() else m.group(0)) if m else None
    return r.returncode


# ---------------------------------------------------------------------------- expectations
OPS = {"eq": lambda v, a: v == a, "ne": lambda v, a: v != a, "lt": lambda v, a: v < a, "le": lambda v, a: v <= a,
       "gt": lambda v, a: v > a, "ge": lambda v, a: v >= a, "in": lambda v, a: v in a,
       "not_in": lambda v, a: v not in a, "match": lambda v, a: re.search(a, str(v)) is not None}


def matches(value, spec) -> bool:
    """scalar = equality; list = membership; {op: arg, ...} = all operators hold."""
    if isinstance(value, str) and value.startswith("error:"):
        return False
    try:
        if isinstance(spec, dict):
            return all(OPS[k](value, a) for k, a in spec.items())
        if isinstance(spec, list):
            return value in spec
        return value == spec
    except TypeError:
        return False


def run_probe(lab, p):
    t = time.time()
    evidence = None
    try:
        v = PROBES[p["kind"]](lab, **p.get("args", {}))
        if isinstance(v, tuple):
            v, evidence = v
    except Exception as e:  # a probe that cannot answer is a value, not a crash
        v = f"error:{type(e).__name__}"
        evidence = str(e)[:200]
        lab.drop(p.get("args", {}).get("node"))
    return v, evidence, round(time.time() - t, 2)


def sample(lab, probes, phase):
    rows, all_ok = [], True
    for p in probes:
        v, ev, dur = run_probe(lab, p)
        judged = phase is not None and phase in p
        ok = matches(v, p[phase]) if judged else None
        if judged and not ok:
            all_ok = False
        row = {"probe": p["id"], "kind": p["kind"], "value": v, "expect": p.get(phase) if phase else None,
               "ok": ok, "s": dur}
        if ev is not None:
            row["evidence"] = ev
        rows.append(row)
    return all_ok, rows


def poll(lab, probes, phase, timeout, interval, stable=1, tick=None):
    """Sample until every judged probe matches `stable` times running. Returns a phase record."""
    t0, streak, first_ok, n, rows = time.time(), 0, None, 0, []
    while True:
        n += 1
        ts = time.time()
        ok, rows = sample(lab, probes, phase)
        if tick:
            tick()
        if ok:
            streak += 1
            first_ok = ts if streak == 1 else first_ok
            if streak >= stable:
                return {"ok": True, "t_first_ok": first_ok, "elapsed_s": round(time.time() - t0, 2),
                        "samples": n, "values": rows}
        else:
            streak, first_ok = 0, None
        if time.time() - t0 > timeout:
            return {"ok": False, "elapsed_s": round(time.time() - t0, 2), "samples": n, "values": rows}
        time.sleep(max(0.0, interval - (time.time() - ts)))
