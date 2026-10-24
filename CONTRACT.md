# Shimpaku — build contract (binding for every builder)

Source of design: research notes `research/2026-10-24-mist-emulation/PLAN.md` (+ R1–R9, REVIEW). This file fixes the
interfaces so parallel builders do not collide. Change it only through the orchestrator.

## Process model

One daemon **`shimpakud`** owns all state (DuckDB allows one read-write process). It runs, in one asyncio
process: the collector, the analytics loop, the executor and validation timers, the FastAPI app (REST + SSE),
and the MCP server (Streamable HTTP mounted at `/mcp`). Everything else is a client:

- `shimpaku mcp-stdio` — stdio MCP server that **proxies** every call to `shimpakud` `/mcp` (for Claude Code /
  OpenCode spawned over `ssh <lab-host>`). It never opens the database.
- `shimpaku assistant` / the dashboard's chat — MCP client of `/mcp` + LLM via LiteLLM.
- `bench` — separate process (later separate Unix user); own NETCONF; reads only the audit API.

## Layout (each builder owns its directory; do not edit others')

| Path | Owner | What |
|---|---|---|
| `src/shimpaku/config.py` | orchestrator | settings from env (below) |
| `src/shimpaku/lock.py` | done | device lease + pending-confirm refusal |
| `src/shimpaku/store/` | B-telemetry | DuckDB schema (`schema.sql`, from R5 07_schema.sql) + SQLite (actions, approvals, tokens, audit) + query helpers |
| `src/shimpaku/topology.py` | B-telemetry | entities/edges from the lab TOMLs + LLDP |
| `src/shimpaku/collector/` | B-telemetry | gnmic subprocess (SAMPLE 10 s + `/junos/events`), parse → store |
| `src/shimpaku/analytics/` | B-analytics | SLE engine (fabric-health device-minute, app-reachability probe-minute), blast radius, detectors, incidents, L1/L2 rule tree |
| `src/shimpaku/devices.py` | B-mcp | PyEZ connection pool + read RPC wrappers |
| `src/shimpaku/actions/` | B-mcp | proposals, policy/guardrails G1–G9, executor (two-leg commit confirmed, lock), validation timers, state machine |
| `src/shimpaku/mcp/` | B-mcp | FastMCP server (tools/resources/prompts), audit middleware (hash chain), stdio proxy |
| `src/shimpaku/api/` | B-web | FastAPI app: REST + SSE, auth flag, sessions, CSRF, tokens page API |
| `src/shimpaku/daemon.py` | orchestrator | wires everything; `shimpakud` entry point |
| `src/shimpaku/assistant/` | B-assistant | loop, LiteLLM client, routing, trace, mocked eval suite |
| `web/` | B-web | React 19 + Vite + TS + Grommet, original theme |
| `bench/` | B-bench | runner, catalogue, scorer (from R9 + R5) |
| `deploy/systemd/` | orchestrator | user units |
| `tests/<area>/` | each owner | pytest; no live lab in unit tests; live checks behind `-m lab` |

**Do not edit `pyproject.toml`** — all dependencies are declared already. Need another? Put it in your report.

## Configuration (env, read only via `shimpaku.config.settings`)

| Var | Default | Meaning |
|---|---|---|
| `SHIMPAKU_DATA` | `~/.local/share/shimpaku` | DuckDB `aiops.duckdb`, SQLite `state.db`, audit `audit.jsonl`, pcaps |
| `SHIMPAKU_LOCK_DB` | `/home/lab/shimpaku/locks.db` | device lease (shared group `shimpaku`) |
| `SHIMPAKU_LAB_REPO` | `~/juniper-7-day-sprint` | topology TOMLs live in `lab/topologies/` |
| `SHIMPAKU_TOPOLOGIES` | `day-02-switching.toml,day-06-datacenter.toml` | sites: `campus`, `fabric` |
| `SHIMPAKU_DEVICE_USER` / `SHIMPAKU_DEVICE_PASSWORD` | `admin` / (lab default) | NETCONF + gNMI |
| `SHIMPAKU_GNMIC` | `~/.local/bin/aiops-gnmic` | gnmic binary |
| `SHIMPAKU_BIND` | `127.0.0.1` | API+MCP bind; may add the the operator's VPN address |
| `SHIMPAKU_PORT` | `8780` | REST, SSE, `/mcp`, and the built dashboard at `/` |
| `SHIMPAKU_AUTH_MODE` | `off` | `off` \| `local` (oidc later). `off` refuses non-loopback bind for MCP |
| `SHIMPAKU_LLM_BASE` | `http://127.0.0.1:4000` | operator's LiteLLM gateway |
| `SHIMPAKU_LLM_KEY` / file `~/.config/shimpaku/litellm.key` | — | gateway key; never logged |
| `SHIMPAKU_LLM_MODELS` | `triage=nvidia-glm-flash,diagnose=tokenharbor-claude-haiku-5.5-free,escalate=nvidia-glm` | role → gateway model |

## Data model

DuckDB `aiops.duckdb` = R5 `07_schema.sql` (entity, entity_edge, metric_sample, event, connect_attempt,
probe_result, user_minute → named **probe_minute**, device_minute, anomaly, incident) plus nothing the agent
must not see. Ground truth never enters this database.
SQLite `state.db`: `action`, `approval`, `token`, `session`, `audit` (hash-chained: `prev_hash`, `row_hash`).

## Service layer (Python, `shimpaku.service` — the ONE API every surface calls)

`shimpaku/service.py` (owner B-mcp, consumers: api, mcp, assistant) exposes async functions returning plain
dicts (JSON-serialisable) — these are 1:1 with MCP T0 tools and REST GETs:

`inventory_list(site=None)`, `sle_get_summary(site, window_min=60)`, `sle_get_blast_radius(incident_id)`,
`iface_get_errors(device, ifname=None)`, `port_get_config(device, ifname)`, `topo_get_neighbors(device=None)`,
`routing_get_health(device=None)`, `config_get_commits(device, n=10)`, `config_get_diff(device, rollback=1)`,
`probe_run(target, kind)`, `incident_list(status=None)`, `incident_get(incident_id)`, `audit_query(...)`,
`action_propose(kind, args, reason, caller)`, `action_get(action_id)`, `action_list(status=None)`,
`action_approve(action_id, user)` (dashboard session only), `action_cancel(action_id, caller)`,
`bench_results()` (reads the published scorer output only).

## MCP (server name `shimpaku`)

Tools exactly as R7 §2.3, tiers T0/T1/T2; T1 = `action_propose_bounce_port`, `action_propose_rerun_deploy`;
T2 = `action_propose_rollback`, `action_propose_disable_port` (MVP); `action_get`, `action_cancel`.
Every tool has `outputSchema`. Resources `lab://topology`, `lab://policy`, `lab://incident/{id}`.
Prompts `triage`, `explain_incident`, `health_check`. Audit middleware on every call.

## REST (prefix `/api`, all JSON; SSE under `/api/stream`)

GET `/api/inventory`, `/api/sle?site=&window=`, `/api/incidents`, `/api/incidents/{id}`,
`/api/incidents/{id}/blast-radius`, `/api/actions`, `/api/actions/{id}`, `/api/audit`, `/api/topology`,
`/api/bench/results`, `/api/whoami`; POST `/api/actions/{id}/approve`, `/api/actions/{id}/cancel`,
`/api/assistant/ask` (streams), `/api/login`, `/api/logout`, `/api/tokens`;
SSE `/api/stream/sle`, `/api/stream/actions`, `/api/stream/events`.

## Brand (operator decision D3, 2026-10-24)

Primary colour **HPE green `#01A982`**. Not used: `grommet-theme-hpe` package, HPE Graphik font, HPE logos,
the green-rectangle Element, product names (Mist, Marvis, Juniper…) as our names, the Mist portal look.
Fonts: Inter + JetBrains Mono, self-hosted. Footer: "Independent project. Not affiliated with HPE or
Juniper Networks." Our status labels: Open / Running / Automatic / Fixed by operator / Verified / Recurred.

## Rules

Verify on the lab, not by intention. Junos writes only via the executor (lock + `commit confirmed`). Lab
access from the dev Mac: `ssh <lab-host>`; code is synced to `~/src/shimpaku` on <lab-host>. Never print secrets.
No HPE/Juniper marks in names. Builders do not commit; the orchestrator commits.
