# Telemetry (B-telemetry) — what was built, what was measured

Owner of `src/shimpaku/store/`, `src/shimpaku/topology.py`, `src/shimpaku/collector/` (CONTRACT.md).
Store API is documented separately: `docs/build/store-api.md`.

## What was built

- **`store/schema.sql` + `store/__init__.py`** — DuckDB `Store` (metrics, events, entities/edges, generic
  `insert_rows` for the other R5 tables) and SQLite `StateDB` (`action`, `approval`, `token`, `session`,
  hash-chained `audit`). `probe_minute` from R5 is named per CONTRACT.md; `action` lives only in SQLite.
- **`topology.py`** — loads `day-02-switching.toml` (`campus`) and `day-06-datacenter.toml` (`fabric`),
  assigns `ge-0/0/<n>` interface names in link order (matches the lab: verified against gNMI `oper-status`
  and addresses), and `enrich()` folds in LLDP neighbours + interface IPv4 from a gNMI ONCE sweep
  (`collector/discover.py`). On the fabric, every LLDP-reported neighbour **confirmed** the TOML-derived
  wiring (`lldp: confirmed_device` — `port-id` on this image is a local ifIndex, not the peer's name, so
  the match is by device + subnet-address agreement, not the raw LLDP port string).
- **`collector/parse.py`** — a stateful per-node parser for gnmic's `--format json` stream: incremental
  JSON decoder (`JSONStream`, handles chunk boundaries mid-value), a SAMPLE-path → metric-name table
  (interface counters/oper-status, CPU, memory, OSPF/BGP/BFD state, now `ospf.state` as the numeric
  RFC 2328 code 0–7 and `ecmp.nexthops` — see "mid-task addition" below), and an event assembler for
  `/junos/events` (one notification per event header, one per attribute, each preceded by a bare delete —
  R2 §2.4's shape, confirmed live) with an **allowlist** (`EVENT_ALLOW_PREFIX`/`EXACT`) that keeps
  protocol/interface/hardware events and drops `UI_*`, `SYSTEM`, `AGENTD_*` session noise.
- **`collector/__init__.py`** — `Collector`/`NodeCollector`: one `gnmic` subprocess per node (credentials
  via `GNMIC_USERNAME`/`GNMIC_PASSWORD` env, never argv), SAMPLE 10 s for counters/CPU/mem/protocol state,
  `target-defined` for `/junos/events`; stdout read incrementally and stall-detected (restarts if no bytes
  for 45 s); exponential backoff with jitter on any failure; per-node `NodeHealth` (state, gap, restarts,
  samples, events kept/dropped) exposed for the API. Also writes `collector.connected` / `collector.lag_s`
  health metrics into the store every 10 s.
- **`collector/poller.py`** (added mid-task per B-analytics' request) — NETCONF `get-probe-results
  owner=shimpaku` every 10 s on the three nodes that carry live RPM probes (`leaf1`, `leaf2`, `sw1`),
  via the shared `shimpaku.devices.pool`; rows go through `shimpaku.analytics.rpm.parse_probe_results`.
  On vJunos 26.2 that parser's assumed XML shape (`probe-test-current-results/probe-test-generic-results`)
  does not match what the device returns (recorded fixture `rpm_sw1.xml`: the results are under
  `results-scope=last test|current test|all tests` siblings, no nested `-current-results` wrapper), so it
  returns `[]`; `poller.rpm_rows()` falls back to `parse_rpm_xml()`, which reads the real shape, and is
  used first by default. `ecmp.nexthops` comes from `get-route-information` on each leaf's peer loopback
  (parsed from `<rt>`/`<nh>`), one gNMI-uncovered path PyEZ fills (R2 §6: gNMI `Get` is config-only).
- **`collector/__main__.py`** — `python -m shimpaku.collector --duration S --db PATH [--discover]
  [--nodes a,b]`, used for every measurement below; never run this against the daemon's live `aiops.duckdb`
  (DuckDB is single-writer).
- Fixtures captured from the live lab (`tests/collector/fixtures/`): `gnmic_leaf2_sample.json` (SAMPLE +
  events, trimmed), `gnmic_leaf2_linkflap.json` (a real port-down/up window), `rpm_sw1.xml`,
  `route_leaf2.xml`, both topology TOMLs.

### Event allowlist, measured

A raw 95 s `/junos/events` capture on leaf2 held **585** notifications; after assembly (585 → single-row
events) and the allowlist, **1 of ~90** events survived locally in that short window — dominated by
`UI_COMMIT_PROGRESS`, `UI_PERMS_DEBUG`, `UI_LOGIN_EVENT`, `UI_AUTH_EVENT`, `UI_NETCONF_CMD`,
`AGENTD_SENSOR_INSTALL_SUCCESS`, matching R2 §2.4's finding almost exactly. Over the 26-minute full-fabric
run (below), **8,892** events arrived and **1,243 kept** (14%) — the keep rate is higher there because
protocol/hardware churn (EVPN/BGP/OSPF-adjacent chatter while other experiments ran concurrently, see
"Open items") is itself allowlisted; pure SSH/commit noise is reliably dropped in both cases.

## Exit criteria — results

### 1. 7/7 nodes, ≥ 20 minutes, gap ≤ 60 s

Ran `python -m shimpaku.collector --duration 1560 --db ~/shp-measure/run1.duckdb --discover` against all
seven nodes (r1, r2, sw1, spine1, spine2, leaf1, leaf2) for **26 minutes** (1560 s), 2026-10-24 05:43–06:09
UTC.

| Node | restarts | max observed SAMPLE-to-SAMPLE gap | events kept / dropped | metric rows |
|---|---|---|---|---|
| r1 | 0 | 9.92 s | 93 / 8,021 | 1,704 |
| r2 | 0 | 8.71 s | 3 / 1,564 | 1,711 |
| sw1 | 0 | 8.91 s | 98 / 1,894 | 4,166 |
| spine1 | 0 | 9.19 s | 98 / 4,827 | 6,194 |
| spine2 | 0 | 8.60 s | 34 / 8,056 | 6,284 |
| leaf1 | 0 | 8.21 s | 588 / 23,335 | 6,554 |
| leaf2 | 0 | 8.88 s | 349 / 6,314 | 6,570 |

**Zero restarts, zero gaps over 60 s on any node** — the worst gap (r1, 9.92 s) is one missed/merged 10 s
sample, well inside the 60 s budget. **Exit criterion met.**

### 2. Event latency on a link-state fault, target ≤ 2 s

Ran a dedicated short collector (`--nodes leaf2,spine1,spine2 --duration 80`) and, mid-run, bounced
leaf2's third NIC (`vnet19` = the leaf2–spine2 link, confirmed by which far-end device reacted):

```
virsh -c qemu:///system domif-setlink lab-leaf2 vnet19 down   # host time 1792824572.69–572.74 (virsh took 55 ms)
# ... 20 s later ...
virsh -c qemu:///system domif-setlink lab-leaf2 vnet19 up     # host time 1792824592.75–592.82
```

No Junos config was touched — this is libvirt dropping the link carrier on the tap device, an F0-style
local fault (R2 §5) with no CLI session on the node. Lag = `ingest_ts − ts` (host arrival − device event
timestamp), exactly as the exit criterion asks:

| Event | Node | Device ts (epoch) | Host ingest (epoch) | **Lag (s)** |
|---|---|---|---|---|
| `SNMP_TRAP_LINK_DOWN` ge-0/0/1 | leaf2 | 1792824570.981 | 1792824572.826 | **1.845** |
| `RPD_OSPF_NBRDOWN` ge-0/0/1.0 | leaf2 | 1792824570.915 | 1792824572.785 | **1.870** |
| `BFDD_STATE_UP_TO_DOWN` | leaf2 | 1792824570.921 | 1792824572.787 | **1.866** |
| `BFDD_STATE_UP_TO_DOWN` (far end) | spine2 | 1792824572.171 | 1792824573.536 | **1.365** |
| `RPD_OSPF_NBRDOWN` (far end) | spine2 | 1792824572.174 | 1792824573.586 | **1.412** |
| `SNMP_TRAP_LINK_UP` ge-0/0/1 | leaf2 | 1792824592.946 | 1792824594.857 | **1.910** |
| `RPD_OSPF_NBRUP` (Init→ExStart) | leaf2 | 1792824592.986 | 1792824594.866 | **1.879** |
| `RPD_OSPF_NBRUP` (→Full) | leaf2 | 1792824593.105 | 1792824594.952 | **1.847** |
| `RPD_OSPF_NBRUP` (far end) | spine2 | 1792824593.495 | 1792824594.845 | **1.350** |

**Every measured lag is under the 2 s target** (max 1.91 s). **Exit criterion met**, with a caveat:

**Caveat — this lag is not pure pipeline latency; it includes device/host clock skew.** Comparing the
device event timestamp against the `date -u` wall clock at the moment the `virsh` command returned shows
leaf2's event timestamps running **~1.7–1.9 s behind** the host clock, spine2 **~0.5 s** behind, spine1
**~0.8 s** behind — each VM drifts by a different, roughly constant amount (no NTP sync was set up for the
lab VMs). The *true* gNMI delivery delay is better bounded by differencing events within one device's own
clock domain: `BFDD_STATE_UP_TO_DOWN` (570.921) to `SNMP_TRAP_LINK_DOWN` (570.981) is 60 ms apart on the
device, and R2 §2.4 independently measured raw `/junos/events` delivery at ~0.9 s on an unloaded link. So
the ~1.8–1.9 s figures above are substantially clock skew, not queueing; the number that satisfies the
literal exit criterion (lag against the device's own reported timestamp) is reported above as asked, but
an incident-timing SLE should not trust `event.ts` across devices without correcting for this skew, or
should key off `event.ingest_ts` consistently instead. Flagged as an open item below.

### 3. Storage, target ≤ 50 MB/day

From the 26-minute, 7-node run (33,183 metric rows, 1,243 events, 0 probe rows — the poller was added
after this run; its own rows are tiny, see below):

| Measure | Value | /day extrapolated |
|---|---|---|
| Raw DuckDB file, after `CHECKPOINT` | 1,847,296 bytes over 1,557 s | **102.5 MB/day** |
| Same rows exported `COPY ... TO parquet (COMPRESSION ZSTD)` | 166,837 bytes (127 KB metrics + 39 KB events + negligible probes) | **9.3 MB/day** |

**The raw DuckDB file, as the collector leaves it, extrapolates to ~103 MB/day — over the 50 MB/day
target.** The same data as zstd Parquet (R5's own measurement method, R5 §7) extrapolates to **~9.3
MB/day**, comfortably under target, and actually smaller than R5's own metric-only estimate (29 MB/day)
because this collector keeps only up/topology-relevant interfaces and a curated counter set, not every
leaf of every interface. The gap is DuckDB's native page/index overhead at this write pattern (many small
`executemany` batches every ~2 s, so compression blocks stay small); a retention job that periodically
compacts `metric_sample`/`event` to Parquet (R5's own storage design, "DuckDB + Parquet") — or simply
batches writes less often and runs `CHECKPOINT`/`VACUUM` on a schedule — should bring live storage close to
the 9.3 MB/day figure. **This is an open item, not closed by this build**: nothing in `store/` or
`collector/` currently compacts `aiops.duckdb`; see "Open items".

RPM probe rows and the two ECMP metrics are cheap: a 35 s live poll against `leaf1`/`leaf2`/`sw1` wrote 3
probe rows and 4 `ecmp.nexthops` samples — at 10 s cadence that is ~250 probe rows/day and ~35k ECMP
samples/day combined, negligible next to the SAMPLE stream.

## Mid-task addition (B-analytics' request)

Added without disturbing the already-measured exit criteria:

- `parse.py`: `ospf.state` numeric code (RFC 2328 order, 0=Down … 7=Full) alongside the existing
  `ospf.full` boolean, so a stuck `ExStart` (the F2 MTU-mismatch signature, R2 §5) is a single comparable
  number, not a string match.
- `collector/poller.py`: a `Poller` that polls `get-probe-results owner=shimpaku` and
  `get-route-information destination=<peer-lo0>` every 10 s via the shared PyEZ pool, writes `probe_result`
  rows (through `shimpaku.analytics.rpm.parse_probe_results`, falling back to a local parser that matches
  the XML vJunos 26.2 actually returns — see "What was built") and `ecmp.nexthops` metrics. Wired into
  `Collector.run()` as a fourth concurrent task; `Collector(..., poll=False)` disables it (used by the
  7-node gNMI-only measurement above, so this addition is not conflated with the exit-criteria numbers).
  Verified live: 0% loss on all three RPM tests (`leaf1 vxlan-l2`, `leaf2 underlay-lo`, `sw1
  users-servers`), `leaf1`/`leaf2` ECMP toward the other leaf's loopback both report 2 next hops.

## Tests

`tests/store/` (3 tests: DuckDB round-trip incl. upsert/replace/size, SQLite actions/approvals/tokens/
sessions, audit hash-chain tamper detection) and `tests/collector/` (14 tests: incremental JSON decoder
across chunk boundaries, SAMPLE-fixture metric extraction incl. filtered-out down ports, event assembly +
allowlist filtering from a real link-flap fixture, `gnmic_config()` never containing a password, topology
load/neighbours/enrich against the real TOMLs incl. address-mismatch and LLDP-confirm/contradict, RPM XML
parsing against the real device shape, ECMP next-hop parsing, and an end-to-end `Poller.poll_node()` against
a fake PyEZ pool). All run with no live lab (`-m lab` not used); `17 passed` locally.

```
.venv/bin/pytest -q tests/store tests/collector
```

## Dependencies needed but not in `pyproject.toml`

None beyond what is already declared (`duckdb`, `pyarrow`, `lxml` via `junos-eznc`'s own dependency,
`pandas`). Noted here per CONTRACT.md rather than edited in.

## Open items / gaps

1. **Storage compaction is unbuilt.** Raw `aiops.duckdb` growth (~103 MB/day at this run's cadence)
   exceeds the 50 MB/day target; the Parquet-equivalent size (~9.3 MB/day) does not, because R5's design
   assumes periodic Parquet compaction that nothing yet runs. Needs an owner (orchestrator or whoever owns
   the retention job) to schedule `CHECKPOINT`/export on a timer.
2. **Event-timestamp clock skew across lab VMs** (leaf2 ~1.8 s, spine1 ~0.8 s, spine2 ~0.5 s behind the
   host, each roughly constant but device-specific) inflates any `ingest_ts − ts` latency measurement and
   would skew cross-device event ordering in analytics (e.g. "did the far end react before or after the
   near end" is unreliable using `ts` alone). No NTP is configured on the lab VMs. Analytics/incident code
   should prefer `ingest_ts` for ordering across devices, or a per-node skew correction should be added.
3. **LLDP port-id is a local ifIndex on this image**, not a port name, so `topology.enrich()` confirms
   lab-TOML wiring by device + subnet-address agreement rather than by matching LLDP's `port-id` string
   directly; this works (12/12 fabric links confirmed) but is worth knowing if the LLDP shape changes.
4. **`shimpaku.analytics.rpm.parse_probe_results` doesn't match the live XML** on vJunos 26.2 (see "What
   was built"); `collector/poller.py` works around it locally (`parse_rpm_xml`). Flagging for B-analytics
   to either fix `parse_probe_results` from the recorded fixture (`tests/collector/fixtures/rpm_sw1.xml`)
   or confirm the workaround should stay.
5. Native UDP JTI and node→host syslog remain unavailable on this host (R2 §2.5, §3, confirmed again
   implicitly — nothing in this build attempts them); all telemetry is gNMI dial-in + NETCONF poll, per
   the architecture decision in PLAN.md §2.
