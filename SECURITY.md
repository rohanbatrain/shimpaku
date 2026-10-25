# Security

Shimpaku can change switch configuration, so its safety model is the product. This page states
it plainly so it can be reviewed.

## Threat model

| Threat | Control | Where |
|---|---|---|
| An LLM (ours or an external agent's) issues a harmful change | Models can only call `action_propose_*`; nothing executes without a human approval from a dashboard session. Approval is not an MCP tool | `actions/engine.py`, `mcp/server.py` |
| Prompt injection via device text (descriptions, commit logs) | Guardrails are Python over live facts, not prompt rules; device text is treated as data | `actions/policy.py` (G0–G9) |
| Argument smuggling (wildcards, `interface-range`, `apply-groups`, Unicode look-alikes, forged `approved: true`) | Exact argument schema; normalised names; inherited config and range membership refused; unknown keys are a block | `actions/policy.py`, `tests/actions/test_policy.py` |
| A guest tester goes beyond the demo | A caller with `role=tester` may propose only `add_vlan_to_trunk` on `sw1 ge-0/0/5`, VLAN 999 (exact args; optional `incident_id`), and only with a `lease_id`; anything else is blocked as `T0 tester scope` before any device is opened, and the proposal is evaluated under a policy narrowed to that port. A tester may approve, reject or cancel only that fixture action, and only if it was proposed under their own lease; a tester's bearer token never approves. Enforced in the engine, so REST and MCP get the same rule. Tester principals come only from a redeemed invite (below), and a tester writes only while their lease is active (a queued tester is refused) | `actions/engine.py:Engine._role_gate`, `Engine._decision_refusal`, `_tester_scope_refusal`, `actions/fixture.py` |
| A lab-wide action with no device (`rerun_deploy`) slips past scope checks | Until 2026-10-24 it skipped G0's scope check and G5 entirely. Now G0 refuses it unless its topology is listed in `write_scope_topologies` (empty by default, so it is refused), and G5 refuses it while any other action is in flight anywhere in the lab. The executor still does not run it | `actions/policy.py:evaluate` |
| Acting on infrastructure links | Trunk, fabric and routed ports refused (except `add_vlan_to_trunk`, trunk-only, demo VLANs only, corroborated on a second device) | G1, G3 |
| Two writers on one device | SQLite lease across processes and users; refuse while a `commit confirmed` is pending | `lock.py` (G5) |
| The executor dies mid-change | Every executor change opens with `commit confirmed` and is confirmed only after a fresh-session health check; if the executor dies, Junos rolls back by itself (measured 73–76 s) | `actions/executor.py` |
| A "fix" that didn't really fix it | Each action is validated on its symptom for a window; recurrence reopens it. The window is the per-kind `validation_window_s` (1800 s), except actions proposed by a tester, which store `tester_validation_window_s` (180 s by default) at propose time | `actions/executor.py`, `actions/engine.py:Engine._window_for` |
| A stranger gets a tester pass, or a link preview burns it | Invites are operator-only (`shimpaku admin invite create`, `POST /api/admin/invites`): 256-bit token from `secrets`, only its SHA-256 stored, 72 h to redeem, single use (atomic), revocable. The link is built from `SHIMPAKU_PUBLIC_URL`, never a Host header. `GET /start` and `GET /api/invites/status` never consume; only `POST /api/invites/redeem` with CSRF and a ticked consent box does. `Referrer-Policy: no-referrer` on those pages | `api/lease.py`, `api/app.py` |
| A tester outlives or widens their pass | One active lease (30 min) and a FIFO queue. The tester session (`__Host-` cookie over https) and the lease's one MCP token (scope `propose`, `expires_at` = lease end, shown once) stop working when the lease ends. Ending (timeout, "End session", operator revoke, invite revoke) is persisted and resumes after a restart: unstarted lease actions cancelled, token and sessions revoked, LiteLLM key deleted, fixture re-seeded as `system` (retried while the lab is busy, given up after 10 min), next lease promoted. Invites, leases and agent tokens are operator-only surfaces (a tester session could list and mint operator tokens before 2026-10-24) | `api/lease.py:LeaseManager`, `daemon.py` |
| Bearer tokens that never die | Tokens carry `expires_at` (existing rows NULL = no expiry, the operator's agents); expired or revoked tokens are refused on REST and `/mcp` | `api/auth.py:AuthStore.token_record` |
| Abuse and spend | In-process token buckets per principal and global (`SHIMPAKU_RATE_LIMITS`, default per minute: writes 30/300, demo ops 6/30, Ask Shim 10/60, MCP `tools/call` 60/600, invite redeem 10/60 per address) → 429 with `Retry-After`; 5 breaks and 5 re-seeds per lease. REST bodies over 64 KiB are refused with 413 before parsing (`/mcp`: 1 MiB); until 2026-10-24 a tester could store megabytes of `args` per proposal in the action and audit rows. Each lease's Ask Shim uses its own LiteLLM virtual key (`max_budget` USD 0.50, `duration` = the lease, `rpm_limit`, `models`) minted with `SHIMPAKU_LLM_ADMIN_KEY` and deleted at lease end; the key lives only in memory. With no admin key, testers have no assistant: it never falls back to the operator's key. Limits reset when the daemon restarts; per-IP limits belong to the edge (build step 8) | `api/ratelimit.py`, `api/llm_keys.py`, `api/app.py` |
| Fault injection as an unaudited write | `demo seed` / `demo break` run through `service.demo_seed` / `service.demo_break`: only the fixture devices (`sw1`, `leaf2`), under the device lock (refused while a `commit confirmed` is pending), one hash-chained audit row per call (refusals and errors included) with the caller's role and lease. Operators, testers with a lease and the system may run them; agents and visitors are refused. The CLI and `POST /api/demo/break|seed` call the same functions. A failed operation's reply and audit row have IP addresses replaced with `<host>` (before 2026-10-24 they carried the device's management address) | `service.py:_demo_op`, `service.py:scrub_hosts`, `actions/fixture.py` |
| An anonymous visitor (public view) does more than look | Only with `SHIMPAKU_PUBLIC_VIEW=1` and auth mode `local` (default off: a lab without the edge stays closed). A caller with no session is the `visitor` principal: GET routes and SSE only; writes, demo operations, lease tokens, operator routes and `/mcp` are refused (403/401); Ask Shim answers 403 "Request a live session to use Shim" before any LLM call. Guest narrowing applies (management addresses dropped from REST inventory, config reads limited and redacted on MCP). Reads are rate-limited per client address (`read`, 120/1200 per minute; the address comes from uvicorn's trusted-proxy handling, so spoofed `X-Forwarded-For` from others is ignored) and logged at INFO without the address; they are not audit rows | `api/app.py:principal_or_none`, `reader`, `ask` |
| The operator's password is guessed, phished or typed on a stranger's laptop (public site) | With `SHIMPAKU_PUBLIC_VIEW=1` operator sign-in needs the argon2id password **and** a TOTP code (RFC 6238: SHA-1, 6 digits, 30 s, one step either side) or a one-time recovery code; with TOTP not set up the login is refused (403, "run `shimpaku admin totp enable`"). A code is accepted once: the last accepted time step is stored and anything at or before it is refused, so a code read off a shoulder or a screen recording is dead. Off public view TOTP is optional and enforced once enabled. Sign-in is limited per real client address (5 a minute in the app, behind Caddy's `login` zone: 10 per 10 min per `client_ip`); every failure is an audit row with the reason only (`bad_credentials`, `bad_totp`, `bad_recovery_code`, `code_missing`, `totp_not_enabled`, `rate_limited`), never the password or code; a success is a row with role `operator` and the factor used | `api/app.py:login`, `api/totp.py:TotpStore.verify_code`, `use_recovery` |
| An operator session left open on a shared computer | In public view operator sessions end after 30 min idle and 4 h absolute (`SHIMPAKU_OPERATOR_IDLE_MIN`, `SHIMPAKU_OPERATOR_MAX_H`; 12 h / 7 d off public view; tester and visitor sessions unchanged). The session cookie never has `Max-Age`/`Expires` (gone when the browser closes). Sign out drops the server session, clears the app's `sessionStorage` and `shimpaku*` `localStorage` keys and reloads the read-only view; `POST /api/logout-all` (operator; "Sign out everywhere" in Settings) deletes every operator session, testers' kept | `api/auth.py:AuthStore.create_session`, `session_record`, `drop_operator_sessions`, `web/src/session.tsx:signOutAndClear` |
| Session requests: spam, enumeration, a link that works too early or too long | `POST /api/session-requests` needs CSRF, consent and passes the `request` limit (3/20 per minute per address; Caddy 5 per 10 min); at most 200 pending (503). The status token is 256-bit, stored as SHA-256; any unknown token is the same 404. Approval is operator-only (REST and `shimpaku admin requests`) and makes a single-use invite with `not_before` = slot − 10 min and expiry = slot end; redeeming early is 425; the lease is capped at slot end + 5 min. `GET /api/invites/ics` and the status page never consume. Exception to "only hashes stored": the approved invite's token is kept in `session_request.invite_token` so the status page can show the link; it is wiped when the invite is used, the slot ends or the request is declined | `api/requests.py`, `api/lease.py`, `api/app.py` |
| Tampering with history | Every MCP tool call, and every REST propose, approve, reject and cancel (refusals included), is a hash-chained audit row carrying the caller's `role` and `lease_id`; a verifier detects an edited row. Rows written before `role`/`lease_id` existed are kept as written and still verify. (Before 2026-10-24 REST approvals reached only the approvals table, not the chain.) | `mcp/audit.py`, `service.py:_audit_row` |
| Ops panels leak a secret or reach non-operators | `/api/ops/host` and `/api/ops/gateway` depend on the app's `operator` dependency (visitor, tester, bearer token, replay: 401/403; tested). The host panel is sensor readings and VM names only, read without root. The gateway panel uses `SHIMPAKU_LLM_ADMIN_KEY` server-side only, shows keys as alias or last 4 of LiteLLM's hashed token, the gateway URL as scheme+host, errors as path + exception type; it never calls LiteLLM `/health` (that runs a paid test call per model). The thermal guard (`SHIMPAKU_THERMAL_GUARD=1`) only warns and records an event; stopping VMs is left to the owner | `api/routes_ops.py`, `hostmon.py`, `gateway.py` |
| Benchmark gaming | Ground truth lives in a separate store owned by a separate Unix user; external agents can run as a sandbox user that cannot reach the lab devices directly, only the MCP endpoint | `bench/`, `ops/phase0-root.sh` |

## Credentials

- No credential is defaulted in code. Device and gateway credentials come from
  `~/.config/shimpaku/env` (mode 600), read by systemd at start; see `.env.example`.
- The LLM gateway key is never logged (tested).
- Agent bearer tokens are stored hashed, scoped `read` or `propose`, never `approve`; a tester's token expires with its lease.
- `SHIMPAKU_LLM_ADMIN_KEY` (LiteLLM master key) is used only to mint and delete per-lease keys; neither is logged or written to disk.

- The TOTP secret and the argon2id hashes of the 8 recovery codes live in `<data>/totp.json`
  (mode 600, beside `admin.hash`); the secret is printed once by `shimpaku admin totp enable`,
  never logged and never in an API response (tested). Recovery codes are shown once: keep them
  offline (e.g. a Bitwarden secure note), never on the lab host or the laptop you demo from.

## Operator over the internet

The owner signs in as operator at `https://app.<domain>` (Cloudflare → Caddy → the daemon in
`auth_mode=local`), sometimes from someone else's laptop. Before turning public view on, run
`shimpaku admin set-password` and `shimpaku admin totp enable` on the lab host. On a shared
computer:

- type the password, never let the browser save it (the field has `autocomplete="off"` in
  public view, but browsers may ignore that: decline the prompt yourself);
- use the authenticator code from your phone; keep the recovery codes in your password manager;
- press **Sign out** when done (the session also ends after 30 min idle, 4 h at most, and when
  the browser closes); if you forgot, **Settings → Sign out everywhere** from any device, or
  `shimpaku admin totp enable --force` plus a new password if the password was exposed;
- if a recovery code was used, `shimpaku admin totp status` shows how many are left.

## Network exposure

- Binds `127.0.0.1` by default. With `SHIMPAKU_AUTH_MODE=off` (every caller is the operator) both the
  daemon (via its MCP endpoint) and `python -m shimpaku.api` refuse any non-loopback bind; before
  2026-10-24 the standalone REST server would serve off mode on `0.0.0.0`. Off mode behind a reverse
  proxy on the same host would still see every peer as loopback: use `local` there.
- `local` mode: argon2id admin password plus TOTP (required with public view), session cookie,
  CSRF double-submit + Origin check, rate-limited sign-in; tester sessions from redeemed invites only.
- `/mcp` answers a missing, invalid, expired or revoked bearer token with HTTP 401 and
  `WWW-Authenticate: Bearer` (MCP 2025-06-18 authorization), a `read` token calling a propose or
  cancel tool with 403, and too many `tools/call` with 429 (`mcp/server.py:McpHttpGate`). With
  `auth_mode=off`, loopback callers still need no token.

## Known gaps for testers

- Guest config reads (DEPLOY-DESIGN D4): a tester or visitor reads port config, commits and config
  diffs only on the fixture's devices (`sw1`, `leaf2`), and every line that could carry a secret
  (passwords, keys, SNMP communities) is redacted. Health, routing, counters, topology and service levels
  stay lab-wide and read-only. Management addresses are withheld (`inventory_list` drops `mgmt_ip`).
- Ask Shim acts with the asker's role: a tester's Shim is held to the tester gate, and a principal that
  cannot propose (a `read` token) gets a `visitor` Shim that cannot propose or cancel (before 2026-10-24
  it was an `agent` and could propose).
- `GET /api/invites/status` is not rate-limited (it only answers valid/invalid for a 256-bit token, so
  guessing is infeasible); redemption is.
- Action records and audit rows name every principal (`tester:<invite id>`, token names) to every reader;
  no note, display name or secret is in them.
- The live daemon's service layer has no `assistant_ask`, so Ask Shim answers 503 there for everyone;
  the per-lease key is minted and passed only where an assistant accepts `llm_key`.
- Red team (build step 6): `tests/redteam/test_tester_redteam.py` attacks tester sessions, tokens, Shim
  and anonymous callers in-process; the defects it found are in `docs/INCIDENTS.md` (#9–#13).

## Personal data

A tester pass stores the operator's invite note and an optional display name. Neither enters the
audit chain, which records only the invite id (`tester:<invite id>`) and the lease id. A daily purge in
the daemon (also `shimpaku admin purge`) deletes both 90 days after the lease ends, or after an unused
invite expired or was revoked; audit rows are never rewritten.

Erasure is self-service; there is no shared mailbox (`SHIMPAKU_PRIVACY_CONTACT` is optional and only
adds an "or write to" line to the notices). A tester presses **Delete my data** on the session banner
(`POST /api/lease/erase`, tester session + CSRF) or on the original invite page, which works even once
the invite is used, expired or revoked (`POST /api/invites/erase` with the invite token, CSRF). Either
wipes the invite note and display name at once, revokes the invite, ends the lease through the normal
cleanup (actions cancelled, token/session/LLM key revoked, fixture re-seeded) and erases the session
request behind a slot-bound invite. Audit row `tester_erase` carries the invite id only.

Session requests store the requester's name, contact (e-mail or LinkedIn URL), message and preferred
times, shown only to the operator. The audit chain records request ids only. The same purge deletes
name, contact, message and decline note 90 days after the slot ended, the decline, or the last
requested time of an expired request.

A requester presses **Delete my data** on their status page (`POST /api/session-requests/erase` with
the status token, CSRF, rate-limited per address as `erase`, default 10/min). In one UPDATE it wipes
name, contact, message, decline note, the preferred times and the approved slot, cancels a pending
request (an approve racing it loses: approve updates only a `pending` row), revokes the invite and
ends any lease bound to it, and leaves the row `erased` with only the id, status and timestamps. An
erased request cannot be approved or declined. Idempotent; audit row `session_request_erase` with
the request id only. Every unknown, malformed or other person's token gets the same 404 on all
three erase routes (the status token, invite token and request id are not interchangeable;
`tests/redteam/test_public_redteam.py`). Session-request intake no longer requires a privacy contact
(it returned 503 without one until 2026-10-25).

## Scope

Built for one isolated lab (no uplink from the lab networks). It has not had an external
security review. Do not point it at production equipment.

## Reporting

Open a private security advisory on the repository, or contact the maintainer directly.
