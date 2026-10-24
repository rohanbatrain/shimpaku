"""python -m shimpaku.collector [--duration S] [--db PATH] — run the collector standalone (dev/measurement).

In production the collector runs inside shimpakud (CONTRACT.md); never run both against one database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time

from shimpaku import topology
from shimpaku.collector import Collector
from shimpaku.store import Store


async def _main(a) -> None:
    store = Store(a.db).init() if a.db else Store().init()
    topo = topology.load()
    if a.discover:
        from shimpaku.collector.discover import discover_all

        lldp, addrs = await discover_all(topo)
        topology.enrich(topo, lldp, addrs)
    topology.sync(store, topo)
    nodes = None
    if a.nodes:
        want = set(a.nodes.split(","))
        nodes = {n["name"]: n["attrs"]["mgmt_ip"] for n in topo.nodes() if n["name"] in want}
    col = Collector(store, topo, nodes=nodes)
    t0 = time.time()
    task = asyncio.create_task(col.run())
    try:
        while not a.duration or time.time() - t0 < a.duration:
            await asyncio.sleep(30)
            print(json.dumps({"t": round(time.time() - t0), "size_mb": round(store.size_bytes() / 1e6, 2),
                              "health": {n: {k: h[k] for k in ("state", "age_s", "max_gap_s", "restarts",
                                                                 "samples", "events_kept", "events_dropped")}
                                         for n, h in col.health().items()}}), flush=True)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        store.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=0)
    ap.add_argument("--db")
    ap.add_argument("--nodes")
    ap.add_argument("--discover", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(_main(a))


if __name__ == "__main__":
    main()
