"""One-shot gNMI reads (Subscribe ONCE) for topology enrichment: LLDP neighbours and interface IPv4 addresses."""

from __future__ import annotations

import asyncio
import os

from shimpaku.collector.parse import JSONStream
from shimpaku.config import settings

PATHS = ["/lldp/interfaces/interface/neighbors/neighbor/state",
         "/interfaces/interface/subinterfaces/subinterface/ipv4/addresses/address/state"]


async def discover(node: str, address: str, timeout_s: float = 40.0) -> list[dict]:
    args = [str(settings.gnmic), "-a", f"{address}:{settings.gnmi_port}", "--insecure", "--timeout", "20s",
            "--format", "json", "subscribe", "--mode", "once"]
    for p in PATHS:
        args += ["--path", p]
    env = {**os.environ, "GNMIC_USERNAME": settings.device_user, "GNMIC_PASSWORD": settings.device_password}
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.DEVNULL, env=env)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        out = b""
    objs = JSONStream().feed(out.decode("utf-8", "replace"))
    for o in objs:
        o.setdefault("source", node)
        if ":" in str(o.get("source")):  # gnmic names the source by address when no target name is given
            o["source"] = node
    return objs


async def discover_all(topo):
    from shimpaku.topology import parse_gnmic_once

    nodes = topo.nodes()
    res = await asyncio.gather(*(discover(n["name"], n["attrs"]["mgmt_ip"]) for n in nodes))
    return parse_gnmic_once([o for r in res for o in r])
