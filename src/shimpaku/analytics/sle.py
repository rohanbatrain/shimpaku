"""SLE engine: fabric-health device-minutes and application-reachability probe-minutes.

Accounting (PLAN §4 fact 1, R5 §1): each (entity, minute, SLE) is good, bad or NA; NA is not
counted; SLE % = good / (good + bad); every bad unit carries exactly one classifier and one
sub-classifier chosen by a fixed precedence, and :func:`assert_classifier_sums` checks that the
classifier units sum to the bad units. Every percentage is reported with its n.

The unit of the application SLE is the **probe-minute**: Junos RPM tests measure the probe
schedule, not people.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from shimpaku.analytics import vocab as V

FABRIC = "fabric_health"
APP = "app_reachability"

# precedence, highest first (R5 §1 table; R9 FAB-01/02/04 expectations)
FABRIC_CLASSIFIERS = ["Adjacency", "Redundancy", "Interface", "Resources"]
APP_CLASSIFIERS = ["Unreachable", "Loss", "Latency"]


def site_role(node: str, attrs: dict | None = None) -> tuple[str, str]:
    """Site and role of a node: entity attrs win, else the lab naming (spine*/leaf* = fabric)."""
    a = attrs or {}
    site = a.get("site")
    role = a.get("role")
    if not site:
        site = "fabric" if node.startswith(("spine", "leaf")) else "campus"
    if not role:
        role = next((r for r in ("spine", "leaf") if node.startswith(r)), "router" if node.startswith("r") else "switch")
    return site, role


def _prep(samples: pd.DataFrame) -> pd.DataFrame:
    s = samples.copy()
    s["metric"] = s["metric"].map(lambda m: V.METRIC_ALIASES.get(m, m))
    for m, fn in V.METRIC_TRANSFORM.items():  # raw store samples (tests, ad-hoc); db.py pre-maps
        alias = V.METRIC_ALIASES[m]
        if (samples["metric"] == m).any():
            sel = samples["metric"].to_numpy() == m
            s.loc[sel, "value"] = s.loc[sel, "value"].map(fn)
            s.loc[sel, "metric"] = alias
    s["ts"] = pd.to_datetime(s["ts"])
    s["minute"] = s["ts"].dt.floor("min")
    s["node"] = s["entity_id"].map(V.node_of)
    return s.sort_values("ts")


# ------------------------------------------------------------------ fabric health
def fabric_minutes(samples: pd.DataFrame, start, end, attrs: dict[str, dict] | None = None) -> pd.DataFrame:
    """Device-minutes for every node with samples, for minutes in [start, end).

    ``samples`` (ts, entity_id, metric, value) must also cover a lookback before ``start``: an
    adjacency, link or ECMP width is *expected* when it was healthy at any earlier sample in the
    frame (the daemon passes 6 h). Absent adjacency samples while the node reports = lost.
    """
    start, end = pd.Timestamp(start).floor("min"), pd.Timestamp(end).floor("min")
    cols = ["minute", "entity_id", "sle", "status", "classifier", "sub_classifier", "evidence", "site", "role"]
    if samples.empty:
        return pd.DataFrame(columns=cols)
    s = _prep(samples)
    last = s.groupby(["minute", "entity_id", "metric"], as_index=False).agg(value=("value", "last"), node=("node", "first"))
    mx = s.groupby(["minute", "node", "metric"])["value"].max()
    present = set(zip(s["minute"], s["node"]))
    rows = []
    attrs = attrs or {}
    for node in sorted(s["node"].unique()):
        ln = last[last.node == node]
        site, role = site_role(node, attrs.get(node))
        by_metric = {m: g.pivot_table(index="minute", columns="entity_id", values="value", aggfunc="last")
                     for m, g in ln.groupby("metric")}
        for minute in pd.date_range(start, end, freq="min", inclusive="left"):
            if (minute, node) not in present:
                rows.append(dict(minute=minute, entity_id=node, sle=FABRIC, status="na",
                                 classifier=None, sub_classifier=None, evidence={}, site=site, role=role))
                continue
            ev = _node_minute(by_metric, mx, node, minute)
            cls, sub = classify_fabric(ev)
            rows.append(dict(minute=minute, entity_id=node, sle=FABRIC,
                             status="bad" if cls else "good", classifier=cls, sub_classifier=sub,
                             evidence=ev, site=site, role=role))
    return pd.DataFrame(rows, columns=cols)


def _lost(p: pd.DataFrame | None, minute, healthy) -> tuple[int, list[dict]]:
    """(expected count, lost entities with their current state) for one adjacency metric."""
    if p is None:
        return 0, []
    hist = p.loc[p.index <= minute]
    if hist.empty:
        return 0, []
    expected = [e for e in hist.columns if (hist[e] == healthy).any()]
    now = hist.loc[minute] if minute in hist.index else None
    lost = []
    for e in expected:
        v = None if now is None or pd.isna(now.get(e)) else float(now[e])
        if v != healthy:
            lost.append(dict(entity=e, value=v))
    return len(expected), lost


def _node_minute(by_metric, mx, node, minute) -> dict:
    ev: dict = {}
    if "ospf_state" in by_metric:  # exact Junos state when the collector sends it
        n_ospf, lost_ospf = _lost(by_metric.get("ospf_state"), minute, V.OSPF_STATE["Full"])
        for d in lost_ospf:
            d["state"] = "absent" if d["value"] is None else V.OSPF_NAME.get(int(d["value"]), str(d["value"]))
    else:  # ospf.full 1/0: listed-but-not-Full is the ExStart/Exchange signature; absent = gone
        n_ospf, lost_ospf = _lost(by_metric.get("ospf_full"), minute, 1.0)
        for d in lost_ospf:
            d["state"] = "absent" if d["value"] is None else "not-Full"
    n_bfd, lost_bfd = _lost(by_metric.get("bfd_up"), minute, 1.0)
    n_bgp, lost_bgp = _lost(by_metric.get("bgp_established"), minute, 1.0)
    ev.update(ospf_expected=n_ospf, ospf_lost=lost_ospf, bfd_expected=n_bfd, bfd_lost=lost_bfd,
              bgp_expected=n_bgp, bgp_lost=lost_bgp)
    # links: physical data ports seen up earlier, now reporting 0
    links_down = []
    p = by_metric.get("oper_up")
    if p is not None and minute in p.index:
        hist = p.loc[p.index <= minute]
        for e in hist.columns:
            ifn = V.ifname_of(e)
            if ifn and V.FABRIC_IF.match(ifn) and (hist[e] == 1).any() and hist.loc[minute, e] == 0:
                links_down.append(e)
    ev["links_down"] = links_down

    def delta(metric):
        q = by_metric.get(metric)
        out = []
        if q is None or minute not in q.index:
            return out
        hist = q.loc[q.index <= minute].ffill()
        if len(hist) < 2:
            return out
        d = hist.iloc[-1] - hist.iloc[-2]
        return [dict(entity=e, delta=float(v)) for e, v in d.items() if pd.notna(v) and v > 0
                and V.ifname_of(e) and V.FABRIC_IF.match(V.ifname_of(e))]

    ev["carrier_delta"] = delta("carrier_transitions")
    ev["error_delta"] = delta("in_errors")
    q = by_metric.get("ecmp_nexthops")
    if q is not None and minute in q.index:
        hist = q.loc[q.index <= minute]
        ev["ecmp_expected"], ev["ecmp"] = float(hist.max().max()), float(hist.loc[minute].min())
    for m in ("cpu_pct", "mem_pct"):
        v = mx.get((minute, node, m))
        ev[m] = None if v is None or pd.isna(v) else float(v)
    return ev


def classify_fabric(ev: dict) -> tuple[str | None, str | None]:
    """One classifier per bad device-minute, by fixed precedence. Returns (None, None) if good."""
    n_ospf, lost_ospf = ev.get("ospf_expected", 0), ev.get("ospf_lost", [])
    if n_ospf and len(lost_ospf) == n_ospf:
        return "Adjacency", "All neighbours down"
    if ev.get("links_down"):
        return "Redundancy", "Link down"           # local carrier loss explains any lost neighbour
    if lost_ospf or ev.get("bfd_lost"):
        stuck = any(d.get("state") in ("ExStart", "Exchange", "Loading", "not-Full") for d in lost_ospf)
        return "Adjacency", "Neighbour stuck" if stuck else "Neighbour down"
    if ev.get("bgp_lost"):
        return "Adjacency", "Overlay session down"
    if ev.get("ecmp") is not None and ev.get("ecmp_expected") and ev["ecmp"] < ev["ecmp_expected"]:
        return "Redundancy", "ECMP reduced"
    if ev.get("carrier_delta"):
        return "Interface", "Flap"
    if ev.get("error_delta"):
        return "Interface", "Errors"
    if (ev.get("cpu_pct") or 0) > V.CPU_BAD_PCT:
        return "Resources", "CPU"
    if (ev.get("mem_pct") or 0) > V.MEM_BAD_PCT:
        return "Resources", "Memory"
    return None, None


# ------------------------------------------------------------------ application reachability
def probe_minutes(probes: pd.DataFrame, start, end) -> pd.DataFrame:
    """Probe-minutes from ``probe_result`` rows (one row per RPM test result).

    Good iff reachable and loss <= 1% and RTT <= 50 ms. Precedence: Unreachable (100% loss or
    not ok) > Loss {with latency, only} > Latency {Network}. A minute with no result is NA.
    """
    cols = ["minute", "client_id", "target", "sle", "status", "classifier", "sub_classifier", "value",
            "threshold_version", "leaf", "access", "evidence", "site", "node"]
    start, end = pd.Timestamp(start).floor("min"), pd.Timestamp(end).floor("min")
    if probes.empty:
        return pd.DataFrame(columns=cols)
    p = probes.copy()
    p["ts"] = pd.to_datetime(p["ts"])
    p["minute"] = p["ts"].dt.floor("min")
    p = p[(p.minute >= start) & (p.minute < end)]
    rows = []
    for (minute, cid, target), g in p.groupby(["minute", "client_id", "target"]):
        loss = float(g["loss_pct"].fillna(100.0).mean())
        rtt = float(g["rtt_ms"].dropna().mean()) if g["rtt_ms"].notna().any() else None
        ok = bool(g["ok"].fillna(False).any())
        unreach = (not ok) or loss >= 100.0
        bad_loss = loss > V.APP_LOSS_PCT
        bad_lat = rtt is not None and rtt > V.APP_RTT_MS
        if unreach:
            cls, sub = "Unreachable", "Total loss"
        elif bad_loss:
            cls, sub = "Loss", "Loss with latency" if bad_lat else "Loss only"
        elif bad_lat:
            cls, sub = "Latency", "Network"
        else:
            cls, sub = None, None
        node = V.node_of(cid)
        site, _ = site_role(node)
        rows.append(dict(minute=minute, client_id=cid, target=target, sle=APP, status="bad" if cls else "good",
                         classifier=cls, sub_classifier=sub, value=rtt, threshold_version=V.THRESHOLD_VERSION,
                         leaf=node, access="rpm", evidence=dict(loss_pct=loss, rtt_ms=rtt, n_results=len(g)),
                         site=site, node=node))
    return pd.DataFrame(rows, columns=cols)


# ------------------------------------------------------------------ summaries
@dataclass
class Summary:
    sle: str
    good: int
    bad: int
    classifiers: list[dict]

    @property
    def n(self) -> int:
        return self.good + self.bad

    @property
    def pct(self) -> float | None:
        return None if self.n == 0 else 100.0 * self.good / self.n

    def label(self) -> str:
        return "no data (n = 0)" if self.n == 0 else f"{self.pct:.1f}% (n = {self.n:,})"

    def as_dict(self) -> dict:
        return dict(sle=self.sle, good=self.good, bad=self.bad, n=self.n,
                    pct=None if self.pct is None else round(self.pct, 2), label=self.label(),
                    classifiers=self.classifiers)


def assert_classifier_sums(units: pd.DataFrame) -> None:
    bad = units[units.status == "bad"]
    assert bad["classifier"].notna().all(), "every bad unit needs a classifier"
    assert bad["sub_classifier"].notna().all(), "every bad unit needs a sub-classifier"
    assert units.loc[units.status != "bad", "classifier"].isna().all(), "good/NA units carry no classifier"
    assert int(bad.groupby("classifier").size().sum()) == len(bad), "classifier units must sum to bad units"


def summarise(units: pd.DataFrame, sle: str) -> Summary:
    u = units[units.sle == sle] if "sle" in units else units
    assert_classifier_sums(u)
    good, bad = int((u.status == "good").sum()), int((u.status == "bad").sum())
    tree = []
    if bad:
        for (c, sc), k in u[u.status == "bad"].groupby(["classifier", "sub_classifier"]).size().items():
            tree.append(dict(classifier=c, sub_classifier=sc, units=int(k), share_pct=round(100 * k / bad, 1)))
        tree.sort(key=lambda d: -d["units"])
        assert sum(d["units"] for d in tree) == bad
    return Summary(sle, good, bad, tree)


def classifier_rank(sle: str, cls: str | None) -> int:
    order = FABRIC_CLASSIFIERS if sle == FABRIC else APP_CLASSIFIERS
    return order.index(cls) if cls in order else len(order)

