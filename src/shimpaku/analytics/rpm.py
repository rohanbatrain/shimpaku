"""Junos RPM probe results → ``probe_result`` rows (application-reachability SLE input).

The collector (B-telemetry) or a PyEZ poll calls ``dev.rpc.get_probe_results(owner=...)`` and
hands the XML here; analytics never talks to devices. Probe ids: ``rpm:<node>:<owner>/<test>``.

**Real shape (vJunos 26.2, captured 2026-10-24, ``tests/collector/fixtures/rpm_sw1.xml``).** There
is no flat ``probe-results-summary``. Each ``probe-test-results`` holds up to three
``probe-test-generic-results`` blocks, told apart by their ``results-scope`` text: "current test"
(the in-progress cycle, possibly short), "last test" (the most recently completed cycle — prefer
this) and "all tests" (cumulative since the probe was created — never use this for a per-minute
reading). Each one carries its own ``probes-sent`` / ``probe-responses`` / ``loss-percentage``
directly (not nested under a summary element), and RTT/jitter are nested further, in µs, under
``probe-test-rtt/probe-summary-results/avg-delay`` and ``.../jitter-delay``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from xml.etree import ElementTree as ET

SCOPE_PREFERENCE = ("last test", "current test", "all tests")


def _f(el, path):
    t = el.findtext(path) if el is not None else None
    try:
        return float(t.strip()) if t is not None else None
    except (ValueError, AttributeError):
        return None


def parse_probe_results(node: str, xml: str | bytes | ET.Element, ts: datetime | None = None) -> list[dict]:
    root = ET.fromstring(xml) if isinstance(xml, (str, bytes)) else xml
    ts = ts or datetime.now(UTC).replace(tzinfo=None)
    rows = []
    for pt in root.iter("probe-test-results"):
        owner, test = pt.findtext("owner"), pt.findtext("test-name")
        target = pt.findtext("target-address")
        g = _pick_scope(pt)
        if g is None:
            continue
        sent = _f(g, "probes-sent")
        recv = _f(g, "probe-responses")
        if recv is None:  # the older/synthetic shape used probes-received
            recv = _f(g, "probes-received")
        loss = _f(g, "loss-percentage")
        if loss is None and sent:
            loss = 100.0 * (1 - (recv or 0) / sent)
        rtt = _f(g, "probe-test-rtt/probe-summary-results/avg-delay")
        jit = _f(g, "probe-test-rtt/probe-summary-results/jitter-delay")
        if rtt is None:  # the older/synthetic shape used flat rtt-average / rtt-jitter
            rtt = _f(g, "rtt-average")
        if jit is None:
            jit = _f(g, "rtt-jitter")
        rows.append(dict(ts=ts, client_id=f"rpm:{node}:{owner}/{test}", target=target, kind="rpm-icmp",
                         ok=bool(recv), rtt_ms=None if rtt is None else rtt / 1000.0,
                         jitter_ms=None if jit is None else jit / 1000.0, loss_pct=loss,
                         dns_ms=None, connect_ms=None, ttfb_ms=None, mos=None))
    return rows


def _pick_scope(pt: ET.Element) -> ET.Element | None:
    """The real shape nests three ``probe-test-generic-results`` under current/last/global result
    wrappers, told apart by ``results-scope``; prefer the most recently *completed* cycle ("last
    test") over an in-progress one ("current test"), and never "all tests" (cumulative)."""
    gens = {(g.findtext("results-scope") or "").strip(): g for g in pt.iter("probe-test-generic-results")}
    for scope in SCOPE_PREFERENCE:
        if scope in gens:
            return gens[scope]
    # the older/synthetic flat shape: probe-results-summary directly under current-results
    flat = pt.find("probe-test-current-results/probe-test-generic-results/probe-results-summary")
    return flat if flat is not None else pt.find(".//probe-results-summary")
