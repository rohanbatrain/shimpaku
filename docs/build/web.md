# B-web build notes: API (`src/shimpaku/api/`) and dashboard (`web/`)

Date 2026-10-24. Builder B-web. Binding: `CONTRACT.md` (REST routes, auth flag, brand D3).

## What exists

| Path | What |
|---|---|
| `src/shimpaku/api/app.py` | `create_app(service, store=None, statedb=None, *, assistant=None, auth_mode=None, web_dist=None, demo=None)` — every CONTRACT REST route, SSE streams, auth, CSRF, security headers, SPA served from `web/dist` at `/` |
| `src/shimpaku/api/auth.py` | argon2id admin, sessions, bearer tokens (SQLite `session` / `token` tables, same DDL as `shimpaku.store`), login rate limit |
| `src/shimpaku/api/cli_admin.py` | `admin_app` Typer group: `shimpaku admin set-password [--stdin] [--print-hash]` → `<SHIMPAKU_DATA>/admin.hash` (0600) |
| `src/shimpaku/api/demo.py` | `SHIMPAKU_DEMO=1` stand-in service with the CONTRACT function names and a canned `assistant_ask`; every payload has `"demo": true`, the UI shows a "Demo data" badge |
| `src/shimpaku/api/__main__.py` | `SHIMPAKU_DEMO=1 python -m shimpaku.api` — API alone, for UI work |
| `tests/api/test_api.py` | 26 tests: auth off/local, cookie flags http vs https, CSRF (token, Origin, Sec-Fetch-Site), token scopes, tokens never approve/reject/manage tokens, rate limit, service error mapping, assistant stream |
| `web/` | React 19 + Vite 8 + TypeScript + Grommet 2.58 (own theme, no `grommet-theme-hpe`) + react-router 7 + Recharts 3; Inter and JetBrains Mono from `@fontsource` (self-hosted, bundled) |
| `docs/screens/` | headless-Chrome screenshots of the **live lab**, taken 2026-10-25: `desktop/` (23 files, 1600×1000 @2x) and `mobile/` (24 files, 390×844 @2x, full page cut at 2600 CSS px; `00-navigation-drawer` and `17-junos-console` are viewport shots) |

## Routes

CONTRACT GETs: `/api/inventory`, `/api/sle?site=&window=`, `/api/incidents`, `/api/incidents/{id}`,
`/api/incidents/{id}/blast-radius`, `/api/actions`, `/api/actions/{id}`, `/api/audit`, `/api/topology`,
`/api/bench/results`, `/api/whoami`. POSTs: `/api/actions/{id}/approve`, `/api/actions/{id}/cancel`,
`/api/assistant/ask` (SSE body), `/api/login`, `/api/logout`, `/api/tokens`.
SSE: `/api/stream/sle?site=&window=` (10 s), `/api/stream/actions` (2 s), `/api/stream/events` (incidents, 5 s);
each polls the service and pushes only when the payload changes.

Additions beyond CONTRACT (for the orchestrator to ratify): `GET /api/settings`, `GET /api/tokens`,
`DELETE /api/tokens/{id}`, `POST /api/actions` (propose, scope `propose`), `POST /api/actions/{id}/reject`
(session only; calls `service.action_reject`). OpenAPI at `/api/docs`.

Service errors (`{"error": {"code"}}`) map to HTTP: not_found/unknown_device 404, bad_state 409, forbidden 403,
bad_argument/propose_failed 422, others 502. `PermissionError` (e.g. `ApprovalRefused`) → 403.
`{"available": false}` passes through as 200; the UI shows "Not collected yet".

## Auth (R8 §5)

- `SHIMPAKU_AUTH_MODE=off`: no sign-in, the browser user is `operator` with approve rights; Settings and the
  top bar warn. CSRF still applies to every cookie-borne write.
- `local`: one `admin`, argon2id hash from `SHIMPAKU_ADMIN_PASSWORD_HASH` or `<data>/admin.hash`. Login limited
  to 5/min per client. Session id is random 32 bytes; only its SHA-256 is stored; rotated at login; 12 h idle
  (sliding `expires_at`), 7 d absolute.
- Cookie: over https `__Host-shimpaku_session` (Secure, HttpOnly, SameSite=Lax, Path=/, no Domain); over plain
  http `shimpaku_session` without Secure, and `/api/settings.cookie_warning` makes the Settings page warn.
- CSRF: readable cookie `(__Host-)shimpaku_csrf`, echoed in `X-CSRF-Token` on every non-GET, plus Origin
  must equal the request origin (or, without Origin, `Sec-Fetch-Site` must be same-origin/none).
  Bearer requests are exempt (no cookies).
- Tokens: `shp_` + 32 random bytes, SHA-256 stored, scope `read` (GETs + assistant) or `propose`
  (+ propose, cancel). No token can approve, reject, or manage tokens (needs a session user).
- The service receives a caller dict `{"kind": "dashboard"|"mcp", "name", "token_id", "transport": "rest"}`
  (fields of `shimpaku.actions.engine.Caller`).

## Assistant contract (for B-assistant)

`create_app(..., assistant=fn)` or `service.assistant_ask`. `fn(question, principal_name, context)` is an
async generator of dicts: `{"type":"meta","model"}`, `{"type":"tool","name","call","ms","ok"}`,
`{"type":"evidence","title","text","href"}` (href = dashboard route, e.g. `/root-cause/INC-0108`),
`{"type":"token","text"}`, `{"type":"done"}`; `{"type":"error","message"}` on failure. Each becomes an SSE event.

## Payload shapes the UI reads

Pages read a UI contract, not the raw service payloads. `api/view.py` maps every real service
payload (`shimpaku.service` / `providers`, and replay snapshots of them) to that contract; `api/app.py`
applies it to each GET route and SSE poller. `api/demo.py` emits the contract directly, and `view.py`
passes it (and any `error` / `available: false` payload) through unchanged. What the real service does
not collect is `null` / `[]` and the page says "not collected" (SLE `goal_pct`, `series`, `changes`;
blast-radius `timeline`, `scores`, per-item impact; action `diff`, `verifier`, `site`, `mode`).
Bench: real data is the harness self-test (`selftest.scenarios`, `totals`); `systems` appears only
when a scored run exists. Tests: `tests/api/test_view.py` over `tests/fixtures/live_snapshot.json`.

Key fields: SLE `sles[].{id,name,domain,goal_pct,value_pct,unit,n,series[{t,value}],
classifiers[{id,name,share_pct}],incident_id}`, `changes[{t,kind:fault|action|commit,text,ref}]`, `sites`;
blast radius `{tree[{name,share_pct,children}],affected[],distribution[{dimension,items[{name,failures,total}]}],
timeline[{t,fail,total}],evidence[{title,detail,tool}],scores}`; action `{steps[{state,t}],guardrails[{id,name,passed}],
diff,validation[{t,level:bad|info|good|pending,text}],verifier{status,detail},series}`; bench `{systems[],per_class[]}`.
Times are epoch seconds (`view.py` parses the service's naive-UTC strings). Missing fields degrade to "—".

## Screens (MVP + extras)

Service levels (`/sle`, site/domain/1h-24h-7d, live SSE), Root cause (`/root-cause`, `/root-cause/:id` with
classifier tree, evidence, Affected / Distribution / Timeline tabs), Actions (`/actions`), Action detail
(`/actions/:id`: lifecycle, guardrails, Junos diff, validation timeline, SLE around the change, verifier,
Approve/Reject dialogs), Benchmark (`/benchmark`), Connected agents (`/agents`), Settings (`/settings`),
Login (`/login` when auth=local). Ask Shim: Ctrl/⌘K or the top-bar button, Esc closes; `?ask=…` deep link.
Status labels: Open / Running / Automatic / Fixed by operator / Verified / Recurred (+ Blocked, Rejected, Cancelled).

Brand: primary `#01A982` for fills with dark ink `#04261B` on it (white on #01A982 is ~3:1); text accent
`#00785C`; dark nav `#0E1A16`; own leaf mark; footer "Independent project. Not affiliated with HPE or Juniper Networks."

## Verification

- `pytest tests/api` — 26 passed (full suite 215 passed at the time of writing).
- `npm run build` — clean `tsc -b` + Vite build (JS bundle ~1 MB, 300 kB gzip; not code-split yet).
- Lighthouse 12 accessibility (`--only-categories=accessibility`): 100 on `/sle`, `/root-cause`, `/root-cause/INC-0107`,
  `/actions`, `/actions/42`, `/benchmark`, `/agents`, `/settings`, `/login` (after fixing a 4.46:1 green pill and
  20 px classifier buttons).
- Screenshots read back. Note: Chrome `--headless --screenshot` does not pump a streaming `fetch`, so the
  assistant shot (07) was taken through the DevTools protocol instead.

## Run

```
cd web && npm install && npm run build
SHIMPAKU_DEMO=1 SHIMPAKU_PORT=8791 .venv/bin/python -m shimpaku.api   # http://127.0.0.1:8791/
# dev: npm run dev (proxies /api to SHIMPAKU_API, default 127.0.0.1:8780)
```

## Open items

- Wire `admin_app` into `shimpaku.cli` (`app.add_typer(admin_app, name="admin")`) — orchestrator owns `cli.py`.
- `SHIMPAKU_DEMO` and `SHIMPAKU_ADMIN_PASSWORD_HASH` are read from `os.environ` (config.py is orchestrator-owned);
  consider adding `demo` / `admin_password_hash` to `Settings`.
- `web/dist` is git-ignored; the deploy must run `npm run build` (or the orchestrator commits dist).
- Not built yet (v2 screens): Insights, Devices, Topology, Clients, Probes, Fault lab.
- No new Python dependencies.
