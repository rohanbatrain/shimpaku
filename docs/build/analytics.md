# Analytics (`shimpaku.analytics`), owner B-analytics

Phase 3 of PLAN.md §5. It covers the fabric-health and application-reachability SLEs, blast radius, detectors,
incidents and the L1/L2 rule tree. The code reuses R5's scripts (`R5-files/01–04`, `r5common.py`), which have
been run. R5's numbers come from simulated data. The numbers on this page are labelled synthetic or live.

## Modules

| File | What it does |
|---|---|
| `vocab.py` | The telemetry vocabulary, reconciled with `docs/build/store-api.md`: entity ids, the metric aliases (`cpu.idle`→`cpu_pct` = 100 − idle, `mem.used_pct`, `if.oper_up`, `if.carrier_transitions`, `if.in_errors`, `ospf.full`, `bfd.up`, `bgp.established`), and the thresholds (CPU 80%, mem 85%, RTT 50 ms, loss 1%). Threshold version `sle-2026.10.24-1` |
| `sle.py` | Builds fabric-health **device-minutes** and application-reachability **probe-minutes**. Each unit is good, bad or NA. `assert_classifier_sums` checks that every bad unit has exactly one classifier and one sub-classifier, and that the classifiers sum to the bad units. `Summary.label()` always prints n, e.g. `93.0% (n = 1,440)` |
| `blast.py` | Applies R5 §2's recommended rule: ≥ 3 failures, one-sided Fisher BH q < 0.05, Laplace RR vs rest ≥ 3, coverage ≥ 0.5, ranked by Bayes factor. Scopes that select the same units are grouped, and the most upstream one is reported. If no scope qualifies, the verdict is **`diffuse`** |
| `detectors.py` | Counts: multi-window binomial (5/15/60 min, p < 1e-4, Beta(1,99) prior on the baseline). Gauges: season-MAD (hour-of-day, \|z\| > 3.5), which becomes Holt-Winters (m = 288 five-minute bins) after ≥ 3 days, with the scale taken from held-out-day forecast errors |
| `rules.py` | The hand-written L1/L2 rule tree, version **`rt-2026.10.24-1`** (R1 link-down, R2 MTU, R3 far-end, R4 BGP overlay, R5 flap, R6 unreachable-with-healthy-fabric → `acl-shadow-term`, R7 lossy/latency, R8 resources). Fault classes use R5 §6.4 strings |
| `incidents.py` | Opens, correlates and closes incidents (rules below) |
| `db.py` | DuckDB access. It accepts a `Store` (and holds `Store._lock`) or a raw connection, and creates the analytics tables only if they are missing (same columns as `store/schema.sql`) |
| `loop.py` | `run_once(store, now=None)` and **`async def run_analytics_loop(store, every_s=60)`**. The loop runs 3 s after each minute boundary, in a worker thread, and logs and survives errors |
| `rpm.py` | `parse_probe_results(node, xml)`: turns Junos `get-probe-results` into `probe_result` rows (`client_id = rpm:<node>:<owner>/<test>`, RTT in ms). Fixed 2026-10-24 against the real vJunos 26.2 shape (`tests/collector/fixtures/rpm_sw1.xml`), which nests three `probe-test-generic-results` blocks under current/last/all-tests scopes instead of one flat `probe-results-summary`; it now prefers "last test" (a completed cycle) the same way B-telemetry's local `parse_rpm_xml` did. The old flat shape still parses too (kept for the earlier synthetic test) |
| `__main__.py` | `python -m shimpaku.analytics --db PATH --minutes N`: a **read-only** replay that writes nothing |

## SLE definitions

**Fabric health** is one unit per node per minute. The node is NA if it sent no sample that minute. "Expected"
means the thing was healthy at some point in the last 6 h of samples, so a neighbour that disappears counts as
lost. Precedence, first match wins:

| Classifier | Sub-classifier | Condition |
|---|---|---|
| Adjacency | All neighbours down | every expected OSPF neighbour lost |
| Redundancy | Link down | a fabric port (`ge/xe/et-x/y/z`, physical only) seen up now reports oper-down. The local carrier loss explains any lost neighbour |
| Adjacency | Neighbour stuck | OSPF neighbour still listed but not Full (ExStart/Exchange signature), port up |
| Adjacency | Neighbour down | OSPF or BFD neighbour gone, local port up (one-sided failure; virtio does not propagate carrier) |
| Adjacency | Overlay session down | BGP peer not Established |
| Redundancy | ECMP reduced | `ecmp.nexthops` below the max seen (optional metric, not collected yet) |
| Interface | Flap / Errors | carrier-transitions or in-errors delta > 0 |
| Resources | CPU / Memory | CPU > 80% (from min idle in the minute) or memory > 85% |

**Application reachability** is one unit per RPM test per minute. A unit is good iff reachable, loss ≤ 1% and
RTT ≤ 50 ms. Precedence is Unreachable {Total loss} > Loss {with latency, only} > Latency {Network}. The unit
is a probe-minute: it measures the probe schedule, not people (PLAN §4 fact 1).

## Incidents

* **Open** (per site, using finalized minutes only). Either of two triggers fires: (T1) one entity is bad in
  each of the last 2 minutes, or 5 minutes if the entity is Resources-only; (T2) the binomial multi-window test
  alarms on the site's bad/n for one SLE, with bad units in ≥ 2 distinct minutes. Resources units are left out
  of T2. A new incident also needs a bad unit in the last finalized minute and none before the site's last
  `t_close`.
* **Correlate**. One open incident per site. Every bad unit in that site, from either SLE, joins it, so FAB-05
  (BGP down plus probes unreachable) is one incident.
* **Primary**. If any probe-minute is bad, the SLE is `app_reachability` and the classifier is its
  highest-precedence one. Otherwise the SLE is `fabric_health`, judged from the service edge: leaf/switch-role
  units first, highest precedence wins. FAB-01 and FAB-02 look symmetric in the data (one side's port down,
  the other side's neighbour lost with its port up). The leaf's view separates them: FAB-01 gives Redundancy,
  FAB-02 gives Adjacency.
* **Close** after 5 quiet minutes. `t_close` = last bad minute + 1 min.
* **Stored** as an `incident` row. `root_causes[0]` = {fault_class, entity, rule, rule_tree, sle, classifier,
  sub_classifier, score}. `blast_radius` = {verdict, scope, group, n, fails, table, affected, classifiers,
  triggers}. `explanation` states n. Virga's `norm_incident` reads `root_causes[].fault_class` and `t_detect`.

Anomalies (gauge detectors) go to `anomaly` every 5 minutes. They do not open incidents.

## Tests (`tests/analytics`, synthetic, no lab): 21 pass on the Mac and 19 of 19 on <lab-host> (before the last 2 were added)

The tests cover: classifier sums and n; that a healthy run is 100% good; precedence; probe-minutes; the RPM
XML parse; **the phase-3 exit on synthetic telemetry**, where FAB-01→Redundancy, FAB-02→Adjacency,
FAB-04→Adjacency (+`mtu-mismatch`), FAB-05→app Unreachable (+`bgp-overlay-down`) and CAM-05→app Unreachable
(+`acl-shadow-term`) each open exactly one incident within 300 s (synthetic detect time is 123 s) and close
at fault end; no fault gives no incident; blast radius scopes leaf1, abstains (`diffuse`) on 20 null seeds,
and refuses a rare single failure; the binomial and season-MAD/Holt-Winters detectors; the rule-tree version;
a run against the real `Store(":memory:")` plus the replay; and a one-minute CPU spike on all 7 nodes, which
opens nothing, while sustained CPU opens a Resources incident.

Run: `.venv/bin/python -m pytest tests/analytics -q`

## Live check on <lab-host> (read-only)

`~/.local/share/shimpaku/aiops.duckdb` did **not exist** on <lab-host>. B-telemetry's measurement collector was
writing `~/shp-measure/run1.duckdb` (started 05:43 UTC). I copied it with its WAL to `~/shp-analytics-check/`
and ran the replay on the copy:

```
ssh <lab-host> 'bash -s' <<'EOF'
cd ~/shp-analytics-check && cp ~/shp-measure/run1.duckdb copy.duckdb && cp ~/shp-measure/run1.duckdb.wal copy.duckdb.wal
cd ~/src/shimpaku && .venv/bin/python -m shimpaku.analytics --db ~/shp-analytics-check/copy.duckdb --minutes 15
EOF
```

**Live result (05:48 UTC, 5 minutes of real data from 7 nodes, window 05:33–05:48):**
* Fabric health was `77.1% (n = 35)`. All 8 bad device-minutes were Resources/CPU, and the classifier sum
  matched (8 = 8). 7 of them fall in **05:43, the minute the collector subscribed**: `cpu.idle` read 0% for
  about 30 s on every node, then 80–87% idle. The 8th is one more leaf1 minute. Outside the subscribe
  minute, fabric health was 27/28. 70 NA device-minutes come from the 10 minutes before the collector
  started (NA is not counted).
* All 20 adjacencies were healthy in the latest sample: OSPF Full on every fabric and campus interface, BFD
  up on 8 sessions, BGP leaf1↔leaf2 Established. The store keys OSPF by interface
  (`leaf1:ospf:ge-0/0/0.0`), and the engine does not depend on that key.
* **No incident opened.** An earlier version of the engine opened two on that one-minute spike (a binomial
  alarm on 7/7 bad in one minute). That is why Resources now needs 5 minutes of persistence and stays out of
  the rate test. The case is now a test.
* App reachability: `no data (n = 0)`, because no RPM probes are configured yet and nothing writes
  `probe_result`.

## Phase-3 exit: how it will be run (not run by me; the bench builder owns injection)

The exit criterion: for FAB-01, FAB-02, FAB-04, FAB-05 and CAM-05, an incident opens with the right classifier
within 300 s of `t_effective`.

Preconditions:
1. `shimpakud` is running, with collector and analytics loop, on `aiops.duckdb`.
2. The orchestrator has applied `docs/build/rpm-config.set` (leaf1 `vxlan-l2` → 10.100.0.42 in HOST, leaf2
   `underlay-lo`, sw1 `users-servers` → 10.20.0.1 in USER), and RPM results land in `probe_result`. That
   needs the collector, or a 10 s PyEZ poll, calling `rpm.parse_probe_results`. **Without RPM rows, FAB-05
   and CAM-05 cannot reach app Unreachable.** FAB-05 would still open as fabric Adjacency/Overlay session down.
3. The fault is held for ≥ 300 s. `virga selftest` reverts as soon as the fault is verified (tens of
   seconds), which is shorter than T1's 2-minute persistence. So the exit uses `virga run`, whose agent
   window holds `detect_budget_s = 300`.

Exact commands, on <lab-host> from `~/src/shimpaku`:

```
.venv/bin/python -m virga plan --seed 3 --count 5 --control-p 0 --only FAB-01,FAB-02,FAB-04,FAB-05,CAM-05
.venv/bin/python -m virga run <PLAN.json printed above> --agent shimpaku --no-decoys
# then, per trial, check classifier + timing straight from the store (read-only):
.venv/bin/python - <<'EOF'
import duckdb, json
c = duckdb.connect('~/.local/share/shimpaku/aiops.duckdb', read_only=True)
for iid, t_open, t_detect, sle, rc in c.execute("SELECT incident_id, t_open, t_detect, sle, root_causes FROM incident ORDER BY t_open").fetchall():
    r = json.loads(rc)[0]; print(iid, t_detect, sle, r['classifier'], r['sub_classifier'], r['fault_class'], r['entity'])
EOF
```

(`python -m virga` with `PYTHONPATH=bench` if virga is not installed. `--agent shimpaku` reads
`GET /api/incidents`, so B-web/B-mcp must expose the `incident` table there.) Pass = for each trial,
`t_detect − t_effective ≤ 300 s` (t_effective from the run's `records.jsonl`), and the expected (sle,
classifier) is FAB-01 fabric_health/Redundancy, FAB-02 fabric_health/Adjacency, FAB-04
fabric_health/Adjacency, FAB-05 app_reachability/Unreachable, CAM-05 app_reachability/Unreachable. Note
that the DuckDB file is held read-write by shimpakud, so the check either goes through the API or runs on
a copy (as above).

## Open items and requests to other builders

* **B-telemetry**: (1) collect RPM results (`get-probe-results`, or the gNMI RPM path if R2 confirms one)
  into `probe_result` with `rpm.parse_probe_results`; (2) optionally emit `ospf.state` as the Junos code
  (1 Down … 8 Full), which names ExStart/Exchange exactly (today `ospf.full = 0` means "not Full");
  (3) optionally `ecmp.nexthops` per leaf to the remote VTEP loopback.
* **Daemon**: share the one `Store` and call `run_analytics_loop(store)`.
* **service / API**: `sle_get_summary` can call `sle.summarise` on `db.units(...)`, and
  `sle_get_blast_radius(incident_id)` returns `incident.blast_radius`.
* Not measured: the null-window rate (≤ 1 incident in 24 h, "fabric only") needs 24 h of a live daemon.
* No new dependencies. Nothing was committed, and no device was changed.
