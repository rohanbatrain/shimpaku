"""Canned Junos output for the console in demo mode (``SHIMPAKU_DEMO=1``): no device is touched.

Shapes follow vJunos 23.x text output; addresses are the demo's 10.99.x documentation-style ones.
Commands the policy allows but this table lacks answer with a short note, never an error.
"""

from __future__ import annotations

import time

DEVICES = ["r1", "r2", "sw1", "spine1", "spine2", "leaf1", "leaf2"]
_MODEL = {"r1": "vjunos-router", "r2": "vjunos-router"}


def _terse(dev: str) -> str:
    rows = ["Interface               Admin Link Proto    Local                 Remote"]
    for i in range(4):
        link = "down" if (dev, i) == ("leaf1", 1) else "up"
        rows.append(f"ge-0/0/{i:<16} up    {link}")
        rows.append(f"ge-0/0/{i}.0{'':<13} up    {link} inet     10.{1 if dev.startswith(('leaf', 'spine')) else 0}.{i}.{DEVICES.index(dev) * 2}/31")
    rows += ["lo0                     up    up", f"lo0.0                   up    up   inet     10.255.0.{DEVICES.index(dev) + 1}/32"]
    return "\n".join(rows) + "\n"


def _ospf(dev: str) -> str:
    out = ["Address          Interface              State           ID               Pri  Dead"]
    peers = {"leaf1": [("ge-0/0/0.0", "spine1")], "leaf2": [("ge-0/0/0.0", "spine1"), ("ge-0/0/1.0", "spine2")],
             "spine1": [("ge-0/0/0.0", "leaf1"), ("ge-0/0/1.0", "leaf2")], "spine2": [("ge-0/0/1.0", "leaf2")],
             "r1": [("ge-0/0/0.0", "r2")], "r2": [("ge-0/0/0.0", "r1")]}.get(dev, [])
    for ifn, peer in peers:
        n = DEVICES.index(peer) + 1
        out.append(f"10.1.0.{n * 2 + 1:<10} {ifn:<22} Full            10.255.0.{n:<8} 128    34")
    return "\n".join(out) + "\n" if peers else "\n"


def _bfd(dev: str) -> str:
    out = ["                                                  Detect   Transmit",
           "Address                  State     Interface      Time     Interval  Multiplier"]
    n = 0
    for ln in _ospf(dev).splitlines()[1:]:
        if ln.strip():
            p = ln.split()
            out.append(f"{p[0]:<24} Up        {p[1]:<14} 0.900     0.300        3")
            n += 1
    out.append(f"\n{n} sessions, {n} clients\nCumulative transmit rate {n * 3.3:.1f} pps, cumulative receive rate {n * 3.3:.1f} pps")
    return "\n".join(out) + "\n"


def _vlans(dev: str) -> str:
    if dev != "sw1":
        return "\n"
    return ("Routing instance        VLAN name             Tag          Interfaces\n"
            "default-switch          default               1\n"
            "default-switch          demo-999              999\n"
            "                                                           ge-0/0/5.0*\n"
            "default-switch          users                 10\n"
            "                                                           ge-0/0/1.0*\n")


def _version(dev: str) -> str:
    return (f"Hostname: {dev}\nModel: {_MODEL.get(dev, 'ex9214')}\nJunos: 23.2R1.14\n"
            "JUNOS OS Kernel 64-bit  [20230608.2c1b5f0_builder_stable_13]\n")


def run(device: str, command: str) -> str:
    time.sleep(0.15)  # a device answers in 120–300 ms (R2 §6); keeps the UI honest about latency
    base = command.split(" | ")[0]
    table = {
        "show interfaces terse": _terse, "show ospf neighbor": _ospf, "show bfd session": _bfd,
        "show vlans": _vlans, "show version": _version,
        "show system alarms": lambda d: "No alarms currently active\n",
        "show chassis alarms": lambda d: "No alarms currently active\n",
        "show system uptime": lambda d: f"Current time: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n"
                                        "System booted: 2026-10-20 08:12:44 UTC (5d 02:11 ago)\n",
    }
    if base in table:
        out = table[base](device)
    elif base.startswith("ping "):
        target = base.split()[1]
        n = int(base.split("count ")[1].split()[0]) if "count " in base else 5
        lines = [f"PING {target} ({target}): 56 data bytes"]
        lines += [f"64 bytes from {target}: icmp_seq={i} ttl=64 time={2.1 + i * 0.13:.3f} ms" for i in range(n)]
        lines += [f"\n--- {target} ping statistics ---", f"{n} packets transmitted, {n} packets received, 0% packet loss",
                  "round-trip min/avg/max/stddev = 2.100/2.400/2.700/0.180 ms"]
        out = "\n".join(lines) + "\n"
    elif base.startswith("traceroute "):
        target = base.split()[1]
        out = f"traceroute to {target} ({target}), 30 hops max, 52 byte packets\n 1  {target}  2.412 ms  2.208 ms  2.199 ms\n"
    else:
        out = f"[demo] no canned output for `{base}` on {device}; the live lab answers it.\n"
    # the allowed filters, applied the way Junos would, so the demo behaves like the device
    for pipe in command.split(" | ")[1:]:
        w = pipe.split()
        lines = out.splitlines()
        if w[0] in ("match", "find"):
            lines = [x for x in lines if w[1].lower() in x.lower()] if w[0] == "match" else \
                next(([*lines[i:]] for i, x in enumerate(lines) if w[1].lower() in x.lower()), [])
        elif w[0] == "except":
            lines = [x for x in lines if w[1].lower() not in x.lower()]
        elif w[0] == "last":
            lines = lines[-int(w[1]) if len(w) > 1 else -10:]
        elif w[0] == "count":
            lines = [f"Count: {len(lines)} lines"]
        out = "\n".join(lines) + "\n"
    return out
