# Integration — wiring the six builders into `shimpakud` (orchestrator)

All six MVP workstreams (telemetry, mcp+actions, analytics, web/api, assistant, bench) were
built in parallel against `CONTRACT.md` and are committed individually. This is the record of
assembling them into one running daemon and proving the result live on <lab-host>, 2026-10-24.

## What was built

- `src/shimpaku/daemon.py` — `shimpakud`: one asyncio process owning the collector, the
  analytics loop, the actions engine (executor + validation timers), the MCP server
  (Streamable HTTP) and the FastAPI app (REST + SSE + dashboard), all on one port. `/mcp` is
  dispatched by a small `CombinedASGI` class to a real second ASGI app rather than a Starlette
  `Mount`, because the dashboard's SPA catch-all (`GET /{path:path}`) would otherwise swallow
  `GET /mcp` (the MCP SSE stream) before a mount at the same path was ever reached. The
  daemon's own resources (Store, collector, analytics, the engine) are started with plain
  `asyncio.create_task`/`async with`, not the ASGI lifespan protocol — multiplexing one
  uvicorn-driven lifespan scope into two independent sub-apps' own protocol handshakes is
  fragile; driving Python context managers directly is not.
- `src/shimpaku/providers.py` — the handful of `service.register_provider` glue functions
  CONTRACT left to the orchestrator: `sle_get_summary`, `sle_get_blast_radius`,
  `incident_list`, `incident_get`, `probe_run`, `bench_results`. No "topology" provider is
  registered: `service.topo_get_neighbors` already falls back to a live LLDP read
  (`devices.lldp_neighbors`) when none is registered, which is strictly better than a static
  blob — this was a real bug in the first draft (see below).
- `src/shimpaku/cli.py` — `shimpaku ask`, `shimpaku admin set-password`, `shimpaku mcp-stdio`,
  assembled from each builder's own module.
- `pyproject.toml` — `bench/virga` added as a second wheel package so `import virga` needs no
  manual `PYTHONPATH` (B-bench's open ask); `--import-mode=importlib` was tried and reverted
  (see bug 3 below) in favour of fixing the three offending test files directly.

## Bugs found only by running the real thing (not caught by any builder's own tests)

1. **DuckDB: two connections, one file, different config → refused.** The first `providers.py`
   draft opened its own `Store(..., read_only=True)` per call. DuckDB (unlike SQLite) refuses a
   second connection to the same file with a different configuration from the same process.
   `/api/sle` failed with exactly that error on the first live run. Fixed by threading the
   daemon's single `Store` into `register_all(store)` and reusing its connection
   (`shimpaku.analytics.db.connection(store)` + `db.lock_of(store)`), matching the pattern
   `analytics/loop.py` already used internally.
2. **Wrong provider contract for "topology".** The first draft read `service.py`'s docstring
   example list too literally and registered a `topology` provider returning a flat
   `{nodes, links}` blob with no `device` parameter. `topo_get_neighbors(device=None)` actually
   calls the provider *with* `device=`, and its intended shape is LLDP neighbours, not a
   topology dump — `/api/topology` failed with `TypeError: unexpected keyword argument
   'device'`. Fixed by reading `service.py`'s real call site (not just its docstring) and
   removing the provider entirely, since the built-in fallback is already correct and live.
3. **Two `conftest.py` files, same bare name, picked up by accident.** `tests/bench/` has its
   own `conftest.py` defining `ROOT`/`DATA`; four `tests/bench/test_*.py` files did
   `from conftest import ROOT` relying on pytest's default import mode inserting `tests/bench/`
   onto `sys.path`. Neither `tests/bench/` nor `tests/mcp/` (which also has a `conftest.py`,
   with no `ROOT`) has an `__init__.py`, so whichever directory's test collection ran first
   won the bare module name `conftest` in `sys.modules` for the rest of the run — a real,
   order-dependent test-isolation bug, invisible when each builder ran only its own directory.
   `--import-mode=importlib` fixes the class of bug but breaks the same bare imports a
   different way (no directory is put on `sys.path` at all under that mode); the actual fix was
   to stop relying on a cross-directory bare import and give each of the four files its own
   `ROOT = Path(__file__).resolve().parents[2]` (the exact expression `tests/bench/conftest.py`
   already used).

None of the six builders' own test suites could have caught any of these: each ran only its
own `tests/<area>/`, DuckDB's single-writer rule only bites with two *different* Store
instances open in the *same process*, and the conftest collision only appears when the *whole*
suite is collected together.

## Live proof, <lab-host>, 2026-10-24 (full log: `/tmp/shimpakud.log` on <lab-host>)

```text
$ SHIMPAKU_DATA=/tmp/shp-daemon-data SHIMPAKU_PORT=8780 python -m shimpaku.daemon
INFO: Uvicorn running on http://127.0.0.1:8780
```

| Check | Result |
|---|---|
| `GET /` (dashboard) | 200 |
| `GET /api/inventory` | 7 real nodes, correct sites |
| `GET /api/sle?site=fabric` | `fabric_health 100.0% (n=2)`, `app_reachability 100.0% (n=3)` — real telemetry, not demo data |
| `GET /api/topology` | real LLDP: `leaf1 ge-0/0/0 → spine1`, etc. |
| `GET /api/incidents` | `{"incidents": []}` (correctly empty — nothing is wrong) |
| `GET /api/bench/results` | `{"available": false, ...}` (correct: no run published to this fresh data dir yet) |
| `GET /api/whoami` | `auth_mode=off` → `authenticated: true`, `user: operator` (CONTRACT's documented `off`-mode behaviour) |
| MCP `initialize` → `tools/call sle_get_summary` over `POST /mcp` on the **same port** | real structured result, matches the REST answer exactly |
| `GET /api/audit` after that call | one hash-chained row: `actor.kind=mcp, transport=http, tool=sle_get_summary, result_status=ok, latency_ms=37.1` |
| `SIGTERM` | "Application shutdown complete" → "shimpakud stopped cleanly", MCP session terminated |
| `deploy.py --validate-only` after everything | `all assertions passed` |

This is the project's central claim proven once, for real: one process, one port, the
dashboard's data and an external agent's MCP call reading the identical live number, and every
tool call landing in a tamper-evident audit trail.

## Full test suite

`pytest -q` (Mac and <lab-host>): **242 passed, 2 skipped** (the two `-m lab` live-device tests,
correctly gated — see B-mcp's `tests/mcp/conftest.py`).

## Known gaps carried into the README (not fixed here — out of scope for integration)

- `bench_results()` reads a fresh daemon's empty data dir; wiring Virga's own publish step to
  write into the *same* `SHIMPAKU_DATA` the daemon uses is an operator/deploy step, not a code
  bug (its own `docs/build/bench.md` already published correctly under its own data dir).
- `action_propose_add_vlan_to_trunk` is in CONTRACT's T1 catalogue but was not built (B-mcp
  built 20 tools; this one is missing) — found live by B-assistant's eval (a model substituted
  an unwarranted `rerun_deploy` for the missing tool). Decision for the operator: add it before
  the pitch, or document the gap and the finding as evidence the guardrails + eval loop works.
- `aiops-bench` Unix-user isolation (REVIEW B2) still needs the operator's `sudo`; Virga ran
  under `~/aiops-bench-store` as documented in its own build doc.
