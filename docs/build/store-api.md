# Store API (`shimpaku.store`) — owner B-telemetry

Stable names; code against these. Schema: `src/shimpaku/store/schema.sql`.

## DuckDB `aiops.duckdb` — `Store`

```python
from shimpaku.store import Store, StateDB, utcnow
s = Store().init()            # path defaults to settings.duckdb_path; Store(":memory:") in tests
s = Store(read_only=True).connect()   # any process other than shimpakud
```

DuckDB allows one read-write process: only `shimpakud` opens it read-write. In the daemon, share **one**
`Store` instance (it serialises calls with a lock).

| Method | Notes |
|---|---|
| `init()` / `connect()` / `close()`; context manager | `init()` creates tables (idempotent) |
| `insert_metrics(rows)` | rows `(ts, entity_id, metric, value)`; bulk via Arrow |
| `insert_events(rows)` | dicts with `ts, ingest_ts, source, node, entity_id, client_id, type, attrs(dict), raw`; `event_id` from a sequence |
| `insert_rows(table, rows)` | generic dict rows (dict/list values become JSON) — use for `device_minute`, `probe_minute`, `anomaly`, `incident`, `probe_result`, `connect_attempt` |
| `upsert_entities(list)` / `replace_edges(list)` | topology (written by `shimpaku.topology.sync(store)`) |
| `query(sql, params) -> list[dict]`, `df(sql, params)`, `execute(sql, params)` | |
| `latest_metrics(entity_prefix, metric_prefix, max_age_s=120)` | last value per (entity, metric) |
| `events(since, node, types, limit)` | newest first |
| `size_bytes()` | file + WAL |

Timestamps: naive **UTC** `TIMESTAMP`. `event.ts` = device time, `event.ingest_ts` = host arrival.

### Entity ids (written by topology.py and the collector)

| Kind | entity_id | parent |
|---|---|---|
| site | `site:campus`, `site:fabric` | — |
| device | `r1`, `leaf2`, … (lab node name) | site |
| interface | `leaf2:ge-0/0/1` (logical units too, e.g. `leaf2:ge-0/0/0.0`, `leaf2:irb.100`) | device |
| link | `link:leaf2-spine1` | site |
| ospf neighbour / bgp peer / bfd peer | `leaf2:ospf:<router-id>`, `leaf2:bgp:<addr>`, `leaf2:bfd:<addr>` (metric rows only) | device |

Edges: `member_of` (device→site, interface→device), `connected_to` (interface↔interface, both directions,
`attrs.link`), `depends_on` (link→its two interfaces).

### Metric names (`metric_sample.metric`), 10 s SAMPLE

| Metric | Entity | Meaning |
|---|---|---|
| `if.in_octets`, `if.out_octets`, `if.in_pkts`, `if.out_pkts`, `if.in_errors`, `if.out_errors`, `if.in_discards`, `if.out_discards`, `if.in_fcs_errors`, `if.carrier_transitions`, `if.in_broadcast_pkts`, `if.in_multicast_pkts` | interface | cumulative counters (compute rates yourself; resets possible) |
| `if.oper_up` | interface | 1 up, 0 otherwise |
| `cpu.user`, `cpu.kernel`, `cpu.idle` (instant %, CPU index ALL or mean) | device | |
| `mem.used_pct`, `mem.used`, `mem.physical` | device | |
| `ospf.full` (1/0), `ospf.state_changes` | `dev:ospf:<rid>` | adjacency on interface in `attrs`? no — interface is not in the id; see `if` events |
| `bgp.established` (1/0), `bgp.established_transitions` | `dev:bgp:<addr>` | |
| `bfd.up` (1/0), `bfd.failure_transitions` | `dev:bfd:<addr>` | |
| `collector.connected` (1/0), `collector.lag_s` | device | per-node collector health, every 10 s |

### Event types (`event.type`)

The Junos event id (e.g. `SNMP_TRAP_LINK_DOWN`, `RPD_OSPF_NBRDOWN`, `BFDD_STATE_UP_TO_DOWN`,
`UI_COMMIT_COMPLETED`), allowlisted in `shimpaku.collector.parse.EVENT_ALLOW`. `attrs` holds the Junos
attributes (`interface-name`, `neighbor-address`, …) plus `message`, `daemon`, `priority`. `entity_id` is
`dev:ifname` when the event names an interface, else the device. `source` = `gnmi`.

## SQLite `state.db` — `StateDB`

```python
db = StateDB().init()          # settings.sqlite_path, WAL
```

| Area | Methods |
|---|---|
| action | `action_insert(dict) -> row` (id `act-…` if absent, status `proposed`), `action_get(id)`, `action_update(id, **fields)`, `action_list(status=None, limit=200)` |
| approval | `approval_add(action_id, user, decision="approved", note=None)`, `approvals_for(action_id)` |
| token | `token_insert(token_id, name, token_hash, scopes, created_by=None)`, `token_get_by_hash(h)` (non-revoked), `token_list()` (no hashes), `token_revoke(id)`, `token_touch(id)` |
| session | `session_insert(session_id, user, csrf, ttl_s=43200)`, `session_get(id)` (unexpired), `session_delete(id)` |
| audit | `audit_append(actor, kind, tool, args, result, status="ok") -> row`, `audit_query(since, actor, tool, limit)`, `audit_verify() -> (ok, first_bad_seq)` |

`action` columns: `action_id, kind, tier, device, args(JSON), reason, caller, incident_id, status, guardrail,
result(JSON), validation(JSON), commit_comment, created_at, updated_at`. JSON columns (`args`, `result`,
`validation`, `scopes`) come back decoded.

Audit chain: `row_hash = sha256(canonical JSON of seq, ts, actor, kind, tool, args, result, status, prev_hash)`;
`prev_hash` of row 1 is 64 zeros. Appends take `BEGIN IMMEDIATE`, so the chain is linear across processes.
Mirroring to `audit.jsonl` is the MCP middleware's job (the returned row is what to write).
