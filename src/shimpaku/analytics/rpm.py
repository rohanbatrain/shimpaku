"""Junos RPM probe results → ``probe_result`` rows (application-reachability SLE input).

The collector (B-telemetry) or a PyEZ poll calls ``dev.rpc.get_probe_results()`` and hands the
XML here; analytics never talks to devices. Probe ids: ``rpm:<node>:<owner>/<test>``.
Fields read (``show services rpm probe-results | display xml``, R2 §4): probe-test-current-results
→ probe-results-summary {probes-sent, probes-received, loss-percentage, rtt-average (µs),
rtt-jitter (µs)}, and target-address.
"""

from __future__ import annotations

from datetime import UTC, datetime
from xml.etree import ElementTree as ET


def _f(el, path):
    t = el.findtext(path)
    try:
        return float(t) if t is not None else None
    except ValueError:
        return None


def parse_probe_results(node: str, xml: str | bytes | ET.Element, ts: datetime | None = None) -> list[dict]:
    root = ET.fromstring(xml) if isinstance(xml, (str, bytes)) else xml
    ts = ts or datetime.now(UTC).replace(tzinfo=None)
    rows = []
    for pt in root.iter("probe-test-results"):
        owner, test = pt.findtext("owner"), pt.findtext("test-name")
        target = pt.findtext("target-address")
        cur = pt.find("probe-test-current-results/probe-test-generic-results/probe-results-summary")
        if cur is None:
            cur = pt.find(".//probe-results-summary")
        if cur is None:
            continue
        sent, recv = _f(cur, "probes-sent"), _f(cur, "probes-received")
        loss = _f(cur, "loss-percentage")
        if loss is None and sent:
            loss = 100.0 * (1 - (recv or 0) / sent)
        rtt = _f(cur, "rtt-average")
        jit = _f(cur, "rtt-jitter")
        rows.append(dict(ts=ts, client_id=f"rpm:{node}:{owner}/{test}", target=target, kind="rpm-icmp",
                         ok=bool(recv), rtt_ms=None if rtt is None else rtt / 1000.0,
                         jitter_ms=None if jit is None else jit / 1000.0, loss_pct=loss,
                         dns_ms=None, connect_ms=None, ttfb_ms=None, mos=None))
    return rows
