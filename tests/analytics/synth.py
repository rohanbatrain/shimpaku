"""Synthetic lab telemetry (shape of the day-06 fabric + day-02 campus). Not lab data."""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

LEAVES, SPINES = ["leaf1", "leaf2"], ["spine1", "spine2"]
CAMPUS = ["r1", "r2", "sw1"]
LO = {"spine1": "10.255.0.31", "spine2": "10.255.0.32", "leaf1": "10.255.0.41", "leaf2": "10.255.0.42"}
# leafX ge-0/0/(s-1) <-> spineS ge-0/0/(leaf-1)
LINKS = [(l, f"ge-0/0/{si}", s, f"ge-0/0/{li}") for li, l in enumerate(LEAVES) for si, s in enumerate(SPINES)]
T0 = pd.Timestamp("2026-10-24 06:00:00")


def healthy_state():
    st = {}
    for n in LEAVES + SPINES + CAMPUS:
        st[(n, "cpu.idle")] = 70.0
        st[(n, "mem.used_pct")] = 60.0
    for l, lif, s, sif in LINKS:
        for n, ifn, peer in ((l, lif, s), (s, sif, l)):
            st[(f"{n}:{ifn}", "if.oper_up")] = 1.0
            st[(f"{n}:{ifn}", "if.carrier_transitions")] = 3.0
            st[(f"{n}:{ifn}", "if.in_errors")] = 0.0
            st[(f"{n}:ospf:{LO[peer]}", "ospf.full")] = 1.0
            st[(f"{n}:bfd:{LO[peer]}", "bfd.up")] = 1.0
    st[("leaf1:bgp:10.255.0.42", "bgp.established")] = 1.0
    st[("leaf2:bgp:10.255.0.41", "bgp.established")] = 1.0
    for l in LEAVES:
        st[(l, "ecmp.nexthops")] = 2.0
    st[("r1:ge-0/0/0", "if.oper_up")] = 1.0
    st[("r1:ospf:10.0.0.2", "ospf.full")] = 1.0
    st[("r2:ospf:10.0.0.1", "ospf.full")] = 1.0
    return st


def apply_fault(st, fault):
    st = dict(st)
    drop = set()
    if fault == "FAB-01":  # leaf1 ge-0/0/0 admin-disabled; spine1 port stays up (virtio)
        st[("leaf1:ge-0/0/0", "if.oper_up")] = 0.0
        st[("leaf1:ge-0/0/0", "if.carrier_transitions")] = 4.0
        drop |= {("leaf1:ospf:10.255.0.31", "ospf.full"), ("leaf1:bfd:10.255.0.31", "bfd.up"),
                 ("spine1:ospf:10.255.0.41", "ospf.full"), ("spine1:bfd:10.255.0.41", "bfd.up")}
        st[("leaf1", "ecmp.nexthops")] = 1.0
        st[("leaf2", "ecmp.nexthops")] = 1.0
    elif fault == "FAB-02":  # spine1 ge-0/0/0 disabled; leaf1 port stays up/up
        st[("spine1:ge-0/0/0", "if.oper_up")] = 0.0
        drop |= {("leaf1:ospf:10.255.0.31", "ospf.full"), ("leaf1:bfd:10.255.0.31", "bfd.up"),
                 ("spine1:ospf:10.255.0.41", "ospf.full"), ("spine1:bfd:10.255.0.41", "bfd.up")}
        st[("leaf1", "ecmp.nexthops")] = 1.0
        st[("leaf2", "ecmp.nexthops")] = 1.0
    elif fault == "FAB-04":  # MTU: leaf1 ExStart, spine1 Exchange, BFD stays Up, ports up
        st[("leaf1:ospf:10.255.0.31", "ospf.full")] = 0.0
        st[("spine1:ospf:10.255.0.41", "ospf.full")] = 0.0
        st[("leaf1", "ecmp.nexthops")] = 1.0
    elif fault == "FAB-05":  # overlay BGP down; underlay fine
        st[("leaf1:bgp:10.255.0.42", "bgp.established")] = 0.0
        drop |= {("leaf2:bgp:10.255.0.41", "bgp.established")}
    return {k: v for k, v in st.items() if k not in drop}


def samples(minutes: int, fault=None, f_start=None, f_end=None, rng=0, cpu_noise=3.0):
    rng = np.random.default_rng(rng)
    base = healthy_state()
    rows = []
    for k in range(minutes * 6):
        ts = T0 + pd.Timedelta(seconds=10 * k)
        m = k // 6
        st = apply_fault(base, fault) if fault and f_start <= m < f_end else base
        for (eid, metric), v in st.items():
            if metric in ("cpu.idle", "mem.used_pct"):
                v = float(np.clip(v + rng.normal(0, cpu_noise), 0, 100))
            rows.append((ts, eid, metric, v))
    return pd.DataFrame(rows, columns=["ts", "entity_id", "metric", "value"])


def probes(minutes: int, fault=None, f_start=None, f_end=None, rng=0):
    rng = np.random.default_rng(rng)
    tests = [("rpm:leaf1:shimpaku/vxlan-l2", "10.100.0.42", "FAB-05"),
             ("rpm:leaf2:shimpaku/underlay-lo", "10.255.0.41", None),
             ("rpm:sw1:shimpaku/users-servers", "10.20.0.1", "CAM-05")]
    rows = []
    for k in range(minutes * 6):
        ts = T0 + pd.Timedelta(seconds=10 * k)
        m = k // 6
        for cid, tgt, kills in tests:
            dead = fault == kills and fault is not None and f_start <= m < f_end
            rtt = None if dead else float(4 + abs(rng.normal(0, 1)))
            rows.append((ts, cid, tgt, "rpm-icmp", not dead, rtt, None, 100.0 if dead else 0.0,
                         None, None, None, None))
    return pd.DataFrame(rows, columns=["ts", "client_id", "target", "kind", "ok", "rtt_ms", "jitter_ms",
                                       "loss_pct", "dns_ms", "connect_ms", "ttfb_ms", "mos"])


def make_db(minutes, fault=None, f_start=None, f_end=None):
    con = duckdb.connect(":memory:")
    s = samples(minutes, fault, f_start, f_end)
    p = probes(minutes, fault, f_start, f_end)
    con.execute("CREATE TABLE metric_sample AS SELECT * FROM s")
    con.execute("CREATE TABLE probe_result AS SELECT * FROM p")
    return con
