# Security

Shimpaku can change switch configuration, so its safety model is the product. This page states
it plainly so it can be reviewed.

## Threat model

| Threat | Control | Where |
|---|---|---|
| An LLM (ours or an external agent's) issues a harmful change | Models can only call `action_propose_*`; nothing executes without a human approval from a dashboard session. Approval is not an MCP tool | `actions/engine.py`, `mcp/server.py` |
| Prompt injection via device text (descriptions, commit logs) | Guardrails are Python over live facts, not prompt rules; device text is treated as data | `actions/policy.py` (G0–G9) |
| Argument smuggling (wildcards, `interface-range`, `apply-groups`, Unicode look-alikes, forged `approved: true`) | Exact argument schema; normalised names; inherited config and range membership refused; unknown keys are a block | `actions/policy.py`, `tests/actions/test_policy.py` |
| A guest tester goes beyond the demo | A caller with `role=tester` may propose only `add_vlan_to_trunk` on `sw1 ge-0/0/5`, VLAN 999 (exact args; optional `incident_id`), and only with a `lease_id`; anything else is blocked as `T0 tester scope` before any device is opened, and the proposal is evaluated under a policy narrowed to that port. A tester may approve, reject or cancel only that fixture action, and only if it was proposed under their own lease; a tester's bearer token never approves. Enforced in the engine, so REST and MCP get the same rule. Tester sessions and tokens are not issued yet (build step 4): today the gate has no principals to apply to | `actions/engine.py:Engine._role_gate`, `Engine._decision_refusal`, `_tester_scope_refusal`, `actions/fixture.py` |
| A lab-wide action with no device (`rerun_deploy`) slips past scope checks | Until 2026-10-24 it skipped G0's scope check and G5 entirely. Now G0 refuses it unless its topology is listed in `write_scope_topologies` (empty by default, so it is refused), and G5 refuses it while any other action is in flight anywhere in the lab. The executor still does not run it | `actions/policy.py:evaluate` |
| Acting on infrastructure links | Trunk, fabric and routed ports refused (except `add_vlan_to_trunk`, trunk-only, demo VLANs only, corroborated on a second device) | G1, G3 |
| Two writers on one device | SQLite lease across processes and users; refuse while a `commit confirmed` is pending | `lock.py` (G5) |
| The executor dies mid-change | Every executor change opens with `commit confirmed` and is confirmed only after a fresh-session health check; if the executor dies, Junos rolls back by itself (measured 73–76 s) | `actions/executor.py` |
| A "fix" that didn't really fix it | Each action is validated on its symptom for a window; recurrence reopens it. The window is the per-kind `validation_window_s` (1800 s), except actions proposed by a tester, which store `tester_validation_window_s` (180 s by default) at propose time | `actions/executor.py`, `actions/engine.py:Engine._window_for` |
| Fault injection as an unaudited write | `demo seed` / `demo break` run through `service.demo_seed` / `service.demo_break`: only the fixture devices (`sw1`, `leaf2`), under the device lock (refused while a `commit confirmed` is pending), one hash-chained audit row per call (refusals and errors included) with the caller's role and lease. Operators, testers with a lease and the system may run them; agents and visitors are refused. The CLI calls the same functions. No REST route exposes them yet | `service.py:_demo_op`, `actions/fixture.py` |
| Tampering with history | Every MCP tool call, and every REST propose, approve, reject and cancel (refusals included), is a hash-chained audit row carrying the caller's `role` and `lease_id`; a verifier detects an edited row. Rows written before `role`/`lease_id` existed are kept as written and still verify. (Before 2026-10-24 REST approvals reached only the approvals table, not the chain.) | `mcp/audit.py`, `service.py:_audit_row` |
| Benchmark gaming | Ground truth lives in a separate store owned by a separate Unix user; external agents can run as a sandbox user that cannot reach the lab devices directly, only the MCP endpoint | `bench/`, `ops/phase0-root.sh` |

## Credentials

- No credential is defaulted in code. Device and gateway credentials come from
  `~/.config/shimpaku/env` (mode 600), read by systemd at start; see `.env.example`.
- The LLM gateway key is never logged (tested).
- Agent bearer tokens are stored hashed, scoped `read` or `propose`, never `approve`.

## Network exposure

- Binds `127.0.0.1` by default. With `SHIMPAKU_AUTH_MODE=off` the MCP HTTP endpoint refuses any
  non-loopback bind.
- `local` mode: argon2id admin password, session cookie, CSRF double-submit + Origin check,
  rate-limited sign-in.

## Scope

Built for one isolated lab (no uplink from the lab networks). It has not had an external
security review. Do not point it at production equipment.

## Reporting

Open a private security advisory on the repository, or contact the maintainer directly.
