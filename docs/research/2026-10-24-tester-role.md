# Tester role: threat model and design (2026-10-24)

Scope: give invited strangers a time-limited **tester** pass to drive the live Missing-VLAN demo
(break → agent proposes → tester approves → validation/recurrence). Anonymous visitors keep the
read-only replay (`api/replay.py`, `is_replay` in `api/app.py`). Research only: no code changed.

Code read: `actions/policy.py`, `actions/engine.py`, `actions/executor.py`, `actions/state.py`,
`api/auth.py`, `api/app.py`, `mcp/server.py`, `mcp/junos_raw.py`, `demo_cli.py`, `daemon.py`,
`assistant/loop.py`, SECURITY.md, DEMO.md, CONTRACT.md, docs/INCIDENTS.md.

## 0. What the code guarantees today, and where it does not

| # | Finding | Where | Severity for testers |
|---|---|---|---|
| A | Any session principal can approve and reject **every** action: `Principal.can_approve` is `kind == "session"`; `session_user` dependency checks only that; `Engine.approve` checks only `caller.kind == "dashboard"`. There is no per-action authorisation. | `api/auth.py:Principal.can_approve`, `api/app.py:session_user`, `actions/engine.py:Engine.approve` | Critical |
| B | `POST /api/actions` lets any proposer propose any catalogue kind with any args; only G0–G9 constrain it. | `api/app.py:propose` → `service.action_propose` | Critical |
| C | The policy write scope is wider than the fixture: `write_scope_ports` and `test_ports` include `leaf2 ge-0/0/2..9` and `spine2 ge-0/0/9`; G6 waives the incident requirement on test ports. So `bounce_port`/`disable_port` on leaf2 test ports pass G0/G6 for any proposer (G1 still checks role/LLDP). | `actions/policy.py:DEFAULT_POLICY`, `evaluate` (G0, G6) | High |
| D | `rerun_deploy` carries no `device`, so the G0 scope check and G5 are skipped; only a filename regex applies. A proposer can propose a redeploy of any lab topology. | `actions/policy.py:evaluate` (`if dev is not None`) | High |
| E | Local mode has exactly one user (`admin`); there is no user table, no role, no expiry other than 12 h idle / 7 d absolute. | `api/auth.py` (`ADMIN_USER`, `SESSION_*`) | High (blocks the feature) |
| F | Bearer tokens have no expiry column; only manual revoke. Scopes are `read`/`propose` (approve is never a scope: good). | `api/auth.py:_SCHEMA`, `create_token`, `token_principal` | Medium |
| G | Fault injection (`demo break`/`seed`) is CLI-only, runs on the lab host, takes the device lock but bypasses the engine and the audit chain. A tester cannot trigger it, and if exposed naively it would be an unaudited Junos write. | `demo_cli.py:break_`, `seed`, `_apply` | High (blocks the feature) |
| H | Rate limiting exists only for sign-in (5/min/client). Nothing limits propose, ask, MCP calls or SSE streams. | `api/auth.py:login_allowed` | Medium |
| I | `POST /api/assistant/ask` needs only `reader`; a `read` token or any session spends LLM budget; `max_steps=8` is the only bound; no per-principal budget. | `api/app.py:ask`, `assistant/loop.py` | Medium (cost) |
| J | Reads are lab-wide: `port_get_config`, `config_get_diff`, `config_get_commits`, `junos_show` (allowlisted `show` prefixes) accept any device. Fine for the owner, but a tester sees all config. | `mcp/server.py` read tools, `mcp/junos_raw.py:ALLOW_PREFIXES` | Low–medium (disclosure only; lab has no secrets beyond config) |
| K | Validation window is 1800 s for every kind; it holds G5 (`open_actions_device`) on sw1 for 30 min, i.e. one tester per half hour. | `DEFAULT_POLICY["validation_window_s"]`, `executor.py:execute` | Demo-blocking |
| L | MCP auth failure raises `MCPError(-32001)` from middleware, so a bad token is a JSON-RPC error, not HTTP 401 with `WWW-Authenticate`. Fine for static-bearer clients; not spec-shaped. | `mcp/server.py:AuditMiddleware.__call__`, `caller_from_request` | Low |
| M | Cancel: a dashboard caller may cancel anyone's action. | `actions/engine.py:Engine.cancel` | Medium (a tester could cancel the owner's work) |
| N | Audit: MCP calls are hash-chained with `caller.as_dict()`. REST propose/approve go through `service` → engine; approvals are recorded in the state store (`record_approval`) — **verify** they also land in the hash-chained `audit.jsonl` (not established in this read). | `mcp/audit.py`, `actions/state.py:record_approval` | Medium |

Already-sound guardrails a tester inherits unchanged: G0 exact args/no smuggling (incl. MCP-layer
extra-arg refusal), G1 trunk-only for `add_vlan_to_trunk`, G3 `demo_vlans=[999]` + live leaf2
corroboration, G5 lease + pending-confirm refusal, `commit confirmed` (2 min) with fresh-session
check, symptom validation and recurrence (INCIDENTS #8 fix), approval not an MCP tool, tokens never
approve, `auth_mode=off` refuses non-loopback MCP.

## 1. What a tester may and may not do

Decision: a tester is a **narrowly scoped human principal**: a session (for approve) plus an
optional bearer token (for their own agent), both bound to one lease.

| Capability | Tester | Enforce at |
|---|---|---|
| Read dashboard (SLE, incidents, actions, audit, topology) | yes | existing `reader` |
| Device config reads (`port_get_config`, `config_get_*`, `junos_show`) | only `sw1`, `leaf2` (fixture + correlation device) | new check in `mcp/server.py` read tools and REST equivalents via a `principal.device_allow` set |
| Inject fault (`demo break`) | yes, once per lease, only while VLAN 999 is present | new `service.demo_break(caller)` (moved from `demo_cli.py`), REST `POST /api/demo/break`, audited |
| Re-seed | no (system does it at lease end) | lease manager |
| Propose | only `add_vlan_to_trunk` with `{device: sw1, ifname: ge-0/0/5, vlan: 999}` (+ optional incident_id) | new `fixture_only` gate in `actions/engine.py:Engine.propose` keyed on caller role, **before** `pol.evaluate`; also a tester policy overlay (`write_scope_ports={"sw1":["ge-0/0/5"]}`, `test_ports` same, kinds={add_vlan_to_trunk}) so G0 refuses even if the gate is bypassed |
| Approve / reject | only actions that (a) are the fixture action and (b) were proposed under **their** lease (by their session, their token, or the assistant acting for them) | `api/auth.py:Principal.can_approve` becomes role-aware; `actions/engine.py:Engine.approve` checks `caller.role=="tester"` ⇒ `action.kind/args == FIXTURE` and `action.caller.lease_id == caller.lease_id` |
| Cancel | own lease's actions only | `Engine.cancel` (fix M for testers) |
| Tokens | may mint exactly one token, scope `propose`, `expires_at = lease end`, `fixture_only=1`; never `approve`; cannot list/revoke others | `api/app.py:create_token` / `list_tokens` (today `session_user` = admin-only by accident of single-user), `api/auth.py` token table |
| Assistant | yes, through a per-lease LLM key (below) | `api/app.py:ask` |
| Settings, policy changes, bench, other tokens | no | new `admin` dependency for `/api/tokens` list/revoke, `/api/settings` writes |

Self-approval note: in the owner's design an agent's yes is not a human's (`Engine.approve`
docstring). Here the tester is the human; letting them approve their own agent's proposal is the
point of the demo, and is acceptable only because the scope is one VLAN on a wired-to-nothing trunk.

## 2. Concurrency, session length, reset

- **Lease**: one active tester lease on the fixture (new table `tester_lease(lease_id, user,
  started, ends, state)` in `state.db`); others queue (FIFO, position shown) or see replay. Lease
  is acquired at sign-in-with-pass, not at first click.
- **Length**: 15 min hard, 5 min idle. Absolute session cap for testers = lease end (override
  `SESSION_ABSOLUTE_S`/`SESSION_IDLE_S` per role in `api/auth.py:session_user`).
- **Demo validation window**: 1800 s is right for production semantics and wrong for a 15-min demo.
  Policy overlay for the fixture action only: `validation_window_s.add_vlan_to_trunk = 180`,
  `validation_interval_s = 15`. That still covers the recurrence beat (DEMO.md: recurrence within
  a minute) and exceeds `commit confirmed` rollback time (measured 73–76 s). Keep 1800 s for
  everything else. Implement as a per-action window stored at propose time (from caller role) so
  the executor (`executor.py:execute`, line computing `window`) reads it from the row, not policy.
- **End of lease**: cancel any non-terminal lease actions (`approved`/`awaiting_approval`); if one is
  `running`/`validating`, wait for it to finish (cap: window + 120 s) then run `seed` through the
  same locked path; revoke the lease's token(s) and session; release the queue. Also a timer
  re-seed every 30 min if no lease is active, and a startup re-seed check (`demo status`).
- **G5 interaction**: the reseed is a writer; it must take `device_lock(sw1)` (it already does in
  `_apply`) and must refuse if a `commit confirmed` is pending (call `lock.pending_confirm`). The
  owner's own actions on sw1 block testers (G5) — correct; show "lab busy".

## 3. Abuse

- **Rate limits** (new small in-process token bucket, keyed by principal and global): propose
  6/min per tester, 20/lease; break 1/lease (2 with a 60 s cooldown if the first already healed);
  approve 6/min; assistant ask 10/lease; MCP `tools/call` 60/min per token, 600/min global;
  SSE streams ≤3 per session. Enforce in `api/app.py` dependencies and `AuditMiddleware` (MCP).
  Also edge rate limit per IP.
- **Lab host offline / daemon unhealthy**: the edge (or a tiny health probe) routes to the
  replay build (`api/replay.py`) and the pass page says "live lab offline, showing replay"; lease
  is not consumed. In-daemon: if `devices.open_device(sw1)` fails (G7 would block anyway), refuse
  new leases.
- **LLM cost**: mint a LiteLLM virtual key per lease via `/key/generate` with `max_budget`
  (e.g. 0.25 USD), `duration` = lease length (e.g. "15m"), `rpm_limit`/`tpm_limit`, and `models`
  limited to the triage/diagnose models; pass that key into the assistant loop for that request
  instead of the daemon's global key. Source: docs.litellm.ai/docs/proxy/virtual_keys ("max_budget",
  "budget_duration", "rpm_limit", "tpm_limit", "duration", "models" on `/key/generate`; fetched
  2026-10-24). Fallback when the gateway has no master-key path: a global daily cap in the daemon.
  Note the operator's models are mostly free-tier, so the binding constraint is rpm, not dollars.
- **Attribution**: every tester principal carries `role="tester"`, `user="tester:<pass-id>"`,
  `lease_id`. `Principal.caller()` must include `role` and `lease_id`; `Caller` (engine) and audit
  rows store them; assistant-originated calls carry `on_behalf_of=tester:<id>`; the break/reseed
  writes get their own audit rows (today they have none). Pass id, not name/email: invitees' names
  stay in an owner-only mapping outside the audit chain (privacy; the chain is shown publicly).
- **Other**: no free-text from testers reaches config (reason text is already `clean_text` and
  never executed); cap `reason` length (already 500). Pass links single-use, expiring (24 h to
  redeem), redeemed into a session cookie (`Secure`, `__Host-`), never a URL token.

## 4. MCP over the internet

Spec: authorization is **OPTIONAL**; when an HTTP server supports it, it SHOULD follow the OAuth 2.1
profile (RFC 9728 protected-resource metadata, `WWW-Authenticate` on 401, audience-bound tokens,
`Authorization: Bearer` on every request, never in the query string, invalid/expired → 401).
(modelcontextprotocol.io/specification/2025-06-18/basic/authorization, fetched 2026-10-24.)
Claude Code supports both a static header (`claude mcp add --transport http <name> <url> --header
"Authorization: Bearer <token>"`) and OAuth; static headers override OAuth
(code.claude.com/docs/en/mcp, fetched 2026-10-24).

Decision: **static per-lease bearer** now (matches today's verifier, works in Claude Code); OAuth
later only if testers need clients without header support. Required changes:

1. TLS terminates at the edge; daemon stays on loopback/VPN; edge forwards only `/mcp`, `/api`, `/`
   and strips client `X-Forwarded-*`/`X-Shimpaku-Client` trust (the label already grants nothing).
2. `auth_mode=local` mandatory when exposed; startup refuses public exposure with `off` (already
   true for MCP bind).
3. Token table gains `expires_at`, `role`, `lease_id`, `fixture_only`; `token_principal` and the
   MCP `_token_verifier` reject expired tokens and return role/lease.
4. MCP propose tools: tester tokens hit the same `Engine.propose` fixture gate (B, C, D closed for
   them); read tools apply the device allowlist (J).
5. Return HTTP 401 + `WWW-Authenticate: Bearer` for missing/invalid tokens at the ASGI dispatcher
   in `daemon.py` before the MCP app (L), 403 for scope.
6. Per-token rate limit in `AuditMiddleware`; token revoked at lease end.

## 5. Implementation slices (ordered; each small, each with tests)

1. **Role on principals** — add `role` (`admin|tester`) and `lease_id` to `Principal`/`Caller` and audit rows; `can_approve` stays true only for admin until slice 4. Files: `api/auth.py`, `actions/engine.py:Caller`, `mcp/audit.py`. Tests: caller round-trip, audit row carries role.
2. **Users table** — `user(name, role, hash|null, expires_at)` with tester rows created from a pass; sessions get role and per-role expiry. Files: `api/auth.py`, `api/cli_admin.py` (`shimpaku admin pass create --minutes 15`). Tests: expired tester session rejected; admin unchanged.
3. **Fixture gate in engine** — `Engine.propose` refuses any tester proposal not equal to the fixture triple (audited `blocked`, reason `T0 tester scope`). Files: `actions/engine.py`, new `actions/fixture.py` (constants shared with `demo_cli.py`). Tests: tester `bounce_port leaf2 ge-0/0/3`, `rerun_deploy`, `vlan 998`, `disable_port` all blocked; admin path unchanged.
4. **Approval authorisation** — tester may approve/reject/cancel only fixture actions of their own lease. Files: `actions/engine.py:approve/reject/cancel`, `api/auth.py:can_approve`. Tests: tester approving admin's action → refused + `record_approval(refused)`; other lease → refused.
5. **Admin-only surfaces** — `/api/tokens` list/revoke and settings require `role==admin`; testers mint one expiring fixture token. Files: `api/app.py`, `api/auth.py` (`expires_at`, `fixture_only`). Tests: expired token 401; tester cannot list tokens; scope approve still 422.
6. **Demo service + audit** — move break/seed into `service.demo_break/demo_seed` with lock, pending-confirm refusal and audit rows; REST `POST /api/demo/break` (tester, once per lease). Files: `demo_cli.py` (thin wrapper), `service.py`, `api/app.py`. Tests: break audited with tester name; second break rate-limited; break refused while an action is validating.
7. **Per-action validation window** — store `window_s` on the action at propose (180 s for tester fixture actions); executor reads it. Files: `actions/engine.py`, `actions/executor.py:execute`, `actions/policy.py` (`demo_validation_window_s`). Tests: tester action ends after 180 s; admin still 1800 s; recurrence still detected within window.
8. **Lease manager** — single lease + FIFO queue, idle/absolute timers, end-of-lease cancel → wait → reseed → revoke; periodic reseed when idle. Files: new `api/lease.py`, `daemon.py`, `api/app.py` (`/api/lease`). Tests: second tester queued; lease end revokes token and reseeds (mock executor); reseed waits on pending confirm.
9. **Read scoping** — tester device allowlist `{sw1, leaf2}` on config/raw reads (REST + MCP). Files: `mcp/server.py`, `service.py`. Tests: tester `junos_show spine1` refused.
10. **Rate limits** — token-bucket helper applied to propose/approve/break/ask/SSE and MCP tools/call, per principal and global. Files: new `api/ratelimit.py`, `api/app.py`, `mcp/server.py`. Tests: 7th propose/min → 429; audit row `rate_limited`.
11. **LLM per-lease key** — mint LiteLLM virtual key (`max_budget`, `duration`, `rpm_limit`, `models`) at lease start, delete at end; assistant uses it for tester asks; `read` tokens can no longer call ask. Files: `assistant/` client, `api/app.py:ask`, `config.py`. Tests: mocked gateway; key never logged (extend existing test).
12. **MCP HTTP auth shape** — 401 + `WWW-Authenticate: Bearer` in `daemon.py` dispatcher for `/mcp`; expiry in `_token_verifier`. Tests: no token → 401 header; expired → 401.
13. **Edge + fallback** — edge TLS, per-IP limits, health-based routing to replay; pass-redeem page. Files: `deploy/`, `web/` (lease banner, queue, break button). Tests: e2e with lab mocked offline → replay.
14. **Red-team pass** — extend the existing MCP red-team suite with a tester token (smuggling, other devices, self-approve other lease, token replay after lease). Files: `tests/mcp/`, `mcp/redteam_llm.py`.

## 6. Claims to update

- SECURITY.md threat-model row 1: "nothing executes without a human approval from a dashboard session" → add that tester sessions approve only the fixture action of their own lease.
- SECURITY.md Credentials: "Agent bearer tokens are stored hashed, scoped read or propose, never approve" → add expiry and `fixture_only` tester tokens.
- SECURITY.md Network exposure: "Binds 127.0.0.1 by default", "`local` mode: argon2id admin password" → describe tester passes, edge TLS, public `/mcp`, rate limits beyond sign-in.
- SECURITY.md Scope: "Built for one isolated lab" — still true, but add "exposed to invited testers via edge; fixture-only writes".
- SECURITY.md new rows: fault injection is an audited write; LLM spend bounded per lease.
- `api/auth.py` module docstring ("a session user may read, propose, cancel and approve") and `api/app.py` docstring ("approve needs a session user").
- DEMO.md: break/propose via the dashboard/tester flow; validation window 180 s for the demo fixture (and the recurrence beat timing); queue/replay fallback.
- CONTRACT.md config table: new vars (tester lease minutes, demo window, LiteLLM master key path for virtual keys); `SHIMPAKU_AUTH_MODE` exposure rule.
- README (not read in this pass): any statement that only the owner can approve, or that the demo is replay-only; verify before editing.

## Sources

- docs.litellm.ai/docs/proxy/virtual_keys — `/key/generate` fields `max_budget`, `budget_duration`, `rpm_limit`, `tpm_limit`, `duration`, `models` (fetched 2026-10-24).
- modelcontextprotocol.io/specification/2025-06-18/basic/authorization — authorization OPTIONAL; Bearer header on every request; 401 for invalid/expired; RFC 9728 metadata when supported (fetched 2026-10-24).
- code.claude.com/docs/en/mcp — `claude mcp add --transport http … --header "Authorization: Bearer …"`; OAuth via `/mcp`; static header overrides OAuth (fetched 2026-10-24).
