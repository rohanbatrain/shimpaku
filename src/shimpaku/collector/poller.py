"""NETCONF polls that gNMI does not cover: RPM probe results and ECMP next-hop counts (every 10 s).

Uses the shared PyEZ pool (`shimpaku.devices.pool`, B-mcp) in a worker thread. Read-only RPCs:
`get-probe-results owner=shimpaku` -> probe_result rows; `get-route-information destination=<lo0>` ->
metric `ecmp.nexthops` on entity `<node>:route:<prefix>`.

Rows are parsed by `shimpaku.analytics.rpm.parse_probe_results` (B-analytics). On vJunos 26.2 the XML has no
`probe-results-summary` (verified 2026-10-24, fixture rpm_sw1.xml), so that parser returns nothing; until it
handles the real shape, `parse_rpm_xml` below produces the same row shape from `probe-test-generic-results`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from xml.etree import ElementTree as ET

from shimpaku.store import utcnow

log = logging.getLogger("shimpaku.collector.poller")

RPM_OWNER = "shimpaku"
RPM_NODES = ("leaf1", "leaf2", "sw1")
ECMP_TARGETS = {"leaf1": ["10.255.0.42"], "leaf2": ["10.255.0.41"]}  # the other leaf's lo0


def _f(el, path):
    t = el.findtext(path) if el is not None else None
    try:
        return float(t.strip()) if t is not None else None
    except ValueError:
        return None


def parse_rpm_xml(node: str, xml: str | bytes, ts: datetime | None = None) -> list[dict]:
    root = ET.fromstring(xml)
    ts = ts or utcnow()
    rows = []
    for pt in root.iter("probe-test-results"):
        owner, test = pt.findtext("owner"), pt.findtext("test-name")
        gens = {(g.findtext("results-scope") or "").strip(): g for g in pt.iter("probe-test-generic-results")}
        g = gens.get("last test")
        if g is None:
            g = gens.get("current test")
        if g is None:
            continue
        sent, recv = _f(g, "probes-sent"), _f(g, "probe-responses")
        loss = _f(g, "loss-percentage")
        if loss is None and sent:
            loss = 100.0 * (1 - (recv or 0) / sent)
        rtt = _f(g, "probe-test-rtt/probe-summary-results/avg-delay")
        jit = _f(g, "probe-test-rtt/probe-summary-results/jitter-delay")
        rows.append(dict(ts=ts, client_id=f"rpm:{node}:{owner}/{test}", target=pt.findtext("target-address"),
                         kind="rpm-icmp", ok=bool(recv), rtt_ms=None if rtt is None else rtt / 1000.0,
                         jitter_ms=None if jit is None else jit / 1000.0, loss_pct=loss,
                         dns_ms=None, connect_ms=None, ttfb_ms=None, mos=None))
    return rows


def parse_nexthops(xml: str | bytes) -> dict[str, int]:
    """inet.0 destination -> number of next hops of the active entry."""
    root = ET.fromstring(xml)
    out = {}
    for tbl in root.iter("route-table"):
        if (tbl.findtext("table-name") or "").strip() != "inet.0":
            continue
        for rt in tbl.iter("rt"):
            dest = (rt.findtext("rt-destination") or "").strip()
            entries = rt.findall("rt-entry")
            act = next((e for e in entries if e.find("current-active") is not None), entries[0] if entries else None)
            if dest and act is not None:
                out[dest] = len(act.findall("nh"))
    return out


def rpm_rows(node: str, xml: str) -> list[dict]:
    try:
        from shimpaku.analytics.rpm import parse_probe_results

        rows = parse_probe_results(node, xml)
    except Exception:  # noqa: BLE001
        rows = []
    return rows or parse_rpm_xml(node, xml)


class Poller:
    def __init__(self, store, interval_s: float = 10.0, rpm_nodes=RPM_NODES, ecmp=ECMP_TARGETS, pool=None):
        self.store, self.interval_s = store, interval_s
        self.rpm_nodes, self.ecmp = tuple(rpm_nodes), dict(ecmp)
        self._pool = pool
        self.health: dict[str, dict] = {}

    @property
    def pool(self):
        if self._pool is None:
            from shimpaku.devices import pool

            self._pool = pool
        return self._pool

    def poll_node(self, node: str) -> tuple[list[dict], list[tuple]]:
        from lxml import etree

        def fn(dev):
            out = {}
            if node in self.rpm_nodes:
                out["rpm"] = etree.tostring(dev.rpc.get_probe_results(owner=RPM_OWNER)).decode()
            for dst in self.ecmp.get(node, []):
                out[dst] = etree.tostring(dev.rpc.get_route_information(destination=dst)).decode()
            return out

        res = self.pool.run(node, fn)
        ts = utcnow()
        probes = rpm_rows(node, res["rpm"]) if "rpm" in res else []
        for p in probes:
            p["ts"] = ts
        metrics = []
        for dst in self.ecmp.get(node, []):
            for prefix, n in parse_nexthops(res[dst]).items():
                metrics.append((ts, f"{node}:route:{prefix}", "ecmp.nexthops", float(n)))
        return probes, metrics

    async def run(self) -> None:
        nodes = sorted(set(self.rpm_nodes) | set(self.ecmp))
        while True:
            t0 = time.time()
            results = await asyncio.gather(*(asyncio.to_thread(self.poll_node, n) for n in nodes),
                                           return_exceptions=True)
            probes, metrics = [], []
            for n, r in zip(nodes, results):
                if isinstance(r, BaseException):
                    self.health[n] = {"ok": False, "error": f"{type(r).__name__}: {r}"[:200], "t": t0}
                    log.warning("poll %s: %s", n, r)
                    continue
                self.health[n] = {"ok": True, "t": t0, "probes": len(r[0])}
                probes += r[0]
                metrics += r[1]
            if probes:
                await asyncio.to_thread(self.store.insert_rows, "probe_result", probes)
            if metrics:
                await asyncio.to_thread(self.store.insert_metrics, metrics)
            await asyncio.sleep(max(0.5, self.interval_s - (time.time() - t0)))
