# Security

Shimpaku can change switch configuration, so its safety model is the product. This page states
it plainly so it can be reviewed.

## Threat model

| Threat | Control | Where |
|---|---|---|
| An LLM (ours or an external agent's) issues a harmful change | Models can only call `action_propose_*`; nothing executes without a human approval from a dashboard session. Approval is not an MCP tool | `actions/engine.py`, `mcp/server.py` |
| Prompt injection via device text (descriptions, commit logs) | Guardrails are Python over live facts, not prompt rules; device text is treated as data | `actions/policy.py` (G0–G9) |
| Argument smuggling (wildcards, `interface-range`, `apply-groups`, Unicode look-alikes, forged `approved: true`) | Exact argument schema; normalised names; inherited config and range membership refused; unknown keys are a block | `actions/policy.py`, `tests/actions/test_policy.py` |
| Acting on infrastructure links | Trunk, fabric and routed ports refused (except `add_vlan_to_trunk`, trunk-only, demo VLANs only, corroborated on a second device) | G1, G3 |
| Two writers on one device | SQLite lease across processes and users; refuse while a `commit confirmed` is pending | `lock.py` (G5) |
| The executor dies mid-change | Every executor change opens with `commit confirmed` and is confirmed only after a fresh-session health check; if the executor dies, Junos rolls back by itself (measured 73–76 s) | `actions/executor.py` |
| A "fix" that didn't really fix it | Each action is validated on its symptom for a window; recurrence reopens it | `actions/executor.py` |
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
