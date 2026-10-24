"""Telemetry collector: one gnmic subprocess per node (gNMI dial-in, R2 §2), parsed into the DuckDB store.

    collector = Collector(store, topology)      # store: shimpaku.store.Store (read-write)
    await collector.run()                        # until cancelled
    collector.health() -> {node: {...}}          # per-node health for the API

Subscriptions per node (SAMPLE 10 s): interface counters + oper-status, CPU + memory, OSPF/BGP/BFD state;
TARGET_DEFINED /junos/events filtered by `parse.allowed_event`. gnmic gets credentials from the environment
(GNMIC_USERNAME/GNMIC_PASSWORD), never argv, so they do not show in `ps`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from shimpaku.collector.parse import JSONStream, NodeParser
from shimpaku.config import settings
from shimpaku.store import utcnow

log = logging.getLogger("shimpaku.collector")

SAMPLE_INTERVAL_S = 10
SUBSCRIPTIONS = {
    "if": {"paths": ["/interfaces/interface/state/counters", "/interfaces/interface/state/oper-status"],
           "mode": "stream", "stream-mode": "sample"},
    "sys": {"paths": ["/system/cpus/cpu/state", "/system/memory/state"], "mode": "stream", "stream-mode": "sample"},
    "proto": {"paths": [
        "/network-instances/network-instance/protocols/protocol/ospfv2/areas/area/interfaces/interface/neighbors/neighbor/state",
        "/network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor/state",
        "/bfd/interfaces/interface/peers/peer/state"], "mode": "stream", "stream-mode": "sample"},
    "ev": {"paths": ["/junos/events"], "mode": "stream", "stream-mode": "target-defined"},
}


def gnmic_config(node: str, address: str, port: int | None = None, interval_s: int = SAMPLE_INTERVAL_S) -> str:
    """YAML (JSON is valid YAML) config for one target, output to stdout as JSON."""
    import json

    subs = {}
    for name, s in SUBSCRIPTIONS.items():
        s = dict(s)
        if s["stream-mode"] == "sample":
            s["sample-interval"] = f"{interval_s}s"
        subs[name] = s
    cfg = {
        "insecure": True, "timeout": "15s", "retry": "5s", "log": False,
        "targets": {f"{address}:{port or settings.gnmi_port}": {"name": node}},
        "subscriptions": subs,
        "outputs": {"out": {"type": "file", "file-type": "stdout", "format": "json"}},
    }
    return json.dumps(cfg, indent=1)


@dataclass
class NodeHealth:
    node: str
    address: str
    state: str = "starting"          # starting | connected | stalled | backoff
    connects: int = 0
    restarts: int = 0
    last_rx: float = 0.0
    last_sample_ts: float = 0.0
    events_kept: int = 0
    events_dropped: int = 0
    samples: int = 0
    max_gap_s: float = 0.0
    last_error: str = ""
    started: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        now = time.time()
        d = dict(self.__dict__)
        d["age_s"] = round(now - self.last_rx, 1) if self.last_rx else None
        return d


class NodeCollector:
    def __init__(self, node: str, address: str, sink, topo_ifaces: set[str] | None = None,
                 gnmic: Path | None = None, stall_s: float = 45.0):
        self.node, self.address, self.sink = node, address, sink
        self.parser = NodeParser(node, topo_ifaces)
        self.health = NodeHealth(node, address)
        self.gnmic = str(gnmic or settings.gnmic)
        self.stall_s = stall_s
        self._proc: asyncio.subprocess.Process | None = None
        self._prev_sample = 0.0

    async def run(self) -> None:
        backoff = 1.0
        while True:
            t0 = time.time()
            try:
                await self._once()
            except asyncio.CancelledError:
                await self._kill()
                raise
            except Exception as e:  # noqa: BLE001 — keep collecting other nodes
                self.health.last_error = f"{type(e).__name__}: {e}"[:300]
                log.warning("collector %s: %s", self.node, self.health.last_error)
            await self._kill()
            self.health.restarts += 1
            self.health.state = "backoff"
            backoff = 1.0 if time.time() - t0 > 120 else min(backoff * 2, 60.0)
            await asyncio.sleep(backoff * (0.8 + 0.4 * random.random()))

    async def _once(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", prefix=f"gnmic-{self.node}-", delete=False) as f:
            f.write(gnmic_config(self.node, self.address))
            cfg = f.name
        env = {**os.environ, "GNMIC_USERNAME": settings.device_user, "GNMIC_PASSWORD": settings.device_password}
        try:
            self._proc = await asyncio.create_subprocess_exec(
                self.gnmic, "--config", cfg, "subscribe", stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, env=env, limit=1 << 22)
            self.health.connects += 1
            self.health.state = "starting"
            err_task = asyncio.create_task(self._drain_stderr())
            flush_task = asyncio.create_task(self._flusher())
            stream = JSONStream()
            try:
                assert self._proc.stdout is not None
                while True:
                    try:
                        chunk = await asyncio.wait_for(self._proc.stdout.read(1 << 16), timeout=self.stall_s)
                    except asyncio.TimeoutError:
                        self.health.state = "stalled"
                        raise RuntimeError(f"no data for {self.stall_s:.0f}s")
                    if not chunk:
                        rc = await self._proc.wait()
                        raise RuntimeError(f"gnmic exited rc={rc}")
                    now = time.time()
                    for obj in stream.feed(chunk.decode("utf-8", "replace")):
                        self.parser.handle(obj, rx=now)
                    if len(stream.buf) > 8 << 20:
                        stream.buf = ""  # corrupt stream guard
                    self._after_parse(now)
            finally:
                flush_task.cancel()
                err_task.cancel()
        finally:
            Path(cfg).unlink(missing_ok=True)

    def _after_parse(self, now: float) -> None:
        h, p = self.health, self.parser
        h.last_rx = now
        if p.last_sample_ts > self._prev_sample:
            if self._prev_sample:
                gap = p.last_sample_ts - self._prev_sample
                h.max_gap_s = max(h.max_gap_s, gap)
            self._prev_sample = p.last_sample_ts
            h.last_sample_ts = p.last_sample_ts
            h.state = "connected"
        self._push()

    def _push(self) -> None:
        m, e = self.parser.take()
        self.health.samples += len(m)
        self.health.events_kept += len(e)
        self.health.events_dropped = self.parser.dropped_events
        if m or e:
            self.sink(m, e)

    async def _flusher(self) -> None:
        while True:
            await asyncio.sleep(0.1)
            self.parser.flush_events(older_than_s=0.2)
            self._push()

    async def _drain_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        while line := await self._proc.stderr.readline():
            s = line.decode("utf-8", "replace").strip()
            if s:
                self.health.last_error = s[:300]
                log.info("gnmic %s: %s", self.node, s[:300])

    async def _kill(self) -> None:
        p, self._proc = self._proc, None
        if p and p.returncode is None:
            p.terminate()
            try:
                await asyncio.wait_for(p.wait(), 5)
            except asyncio.TimeoutError:
                p.kill()
                await p.wait()


class Collector:
    """All nodes. Batches rows into the store every `flush_s` and writes per-node health metrics."""

    def __init__(self, store, topology=None, nodes: dict[str, str] | None = None, flush_s: float = 2.0,
                 poll: bool = True):
        from shimpaku import topology as topo_mod

        self.store = store
        self.topology = topology or topo_mod.load()
        if nodes is None:
            nodes = {n["name"]: n["attrs"]["mgmt_ip"] for n in self.topology.nodes()}
        self.flush_s = flush_s
        self._metrics: list[tuple] = []
        self._events: list[dict] = []
        self.nodes = {n: NodeCollector(n, ip, self._sink, self.topology.interfaces_of(n)) for n, ip in nodes.items()}
        self.poller = None
        if poll:
            from shimpaku.collector.poller import ECMP_TARGETS, RPM_NODES, Poller

            self.poller = Poller(store, rpm_nodes=[n for n in RPM_NODES if n in nodes],
                                 ecmp={n: v for n, v in ECMP_TARGETS.items() if n in nodes})
        self.listeners: list = []   # callables(events) — e.g. SSE fan-out in the API

    def _sink(self, metrics: list[tuple], events: list[dict]) -> None:
        self._metrics.extend(metrics)
        self._events.extend(events)

    def take(self) -> tuple[list[tuple], list[dict]]:
        m, e = self._metrics, self._events
        self._metrics, self._events = [], []
        return m, e

    def flush(self) -> tuple[int, int]:
        return self.write(*self.take())

    def write(self, m: list[tuple], e: list[dict]) -> tuple[int, int]:
        if m:
            self.store.insert_metrics(m)
        if e:
            self.store.insert_events(e)
            for fn in self.listeners:
                try:
                    fn(e)
                except Exception:  # noqa: BLE001
                    log.exception("event listener failed")
        return len(m), len(e)

    def health(self) -> dict[str, dict]:
        out = {n: c.health.as_dict() for n, c in self.nodes.items()}
        for n, h in (self.poller.health if self.poller else {}).items():
            if n in out:
                out[n]["netconf_poll"] = h
        return out

    def _health_rows(self) -> list[tuple]:
        ts, now = utcnow(), time.time()
        rows = []
        for n, c in self.nodes.items():
            h = c.health
            fresh = h.state == "connected" and h.last_rx and now - h.last_rx < 30
            rows.append((ts, n, "collector.connected", 1.0 if fresh else 0.0))
            if h.last_sample_ts:
                rows.append((ts, n, "collector.lag_s", round(now - h.last_sample_ts, 2)))
        return rows

    async def run(self) -> None:
        tasks = [asyncio.create_task(c.run(), name=f"collector-{n}") for n, c in self.nodes.items()]
        if self.poller:
            tasks.append(asyncio.create_task(self.poller.run(), name="collector-poller"))
        last_health = 0.0
        try:
            while True:
                await asyncio.sleep(self.flush_s)
                if time.time() - last_health >= SAMPLE_INTERVAL_S:
                    self._metrics.extend(self._health_rows())
                    last_health = time.time()
                await asyncio.to_thread(self.write, *self.take())
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.flush()


__all__ = ["Collector", "NodeCollector", "NodeHealth", "gnmic_config", "SUBSCRIPTIONS"]
