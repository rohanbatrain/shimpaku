# Defects found by running the system

Every defect below passed its unit tests and was caught only by running Shimpaku against the
live lab. Each has a fix and, where it could recur, a regression test that fails on the old code.

| # | Date | Defect | Impact | Fix | Regression test |
|---|---|---|---|---|---|
| 1 | 2026-10-24 | The RPM probe parser was written from documentation, not the device's real XML | App-reachability SLE would have read no data | Parser matched to captured vJunos 26.2 XML | `tests/analytics` (real fixture) |
| 2 | 2026-10-24 | A second DuckDB connection to the same file from one process | `/api/sle` failed at runtime | Providers share the daemon's single connection | integration run (`docs/build/integration.md`) |
| 3 | 2026-10-24 | A data provider registered with the wrong contract | `/api/topology` failed | Removed; the built-in live LLDP read is used | integration run |
| 4 | 2026-10-24 | Two test directories' `conftest` modules collided by name | Order-dependent test failures | Explicit paths in the affected tests | full-suite run |
| 5 | 2026-10-24 | `sle_get_summary` ignored its `site` argument | The campus view showed fabric events | Filter on each unit's own site | live check, both sites |
| 6 | 2026-10-24 | The executor's own re-check didn't know trunk ports were valid for `add_vlan_to_trunk` | The approved action failed at execution | Role check per action kind | `tests/actions/test_engine.py` |
| 7 | 2026-10-24 | An action's error string collided with the API's structured `error` field | `action_get` crashed for failed actions | Normalised at the service layer | `tests/mcp/test_service_errors.py` |
| 8 | 2026-10-24 | **`add_vlan_to_trunk` validated device health, not the VLAN** | **A false "verified":** action `act-20261024-9b7065` was marked verified at 09:17:26 UTC although the VLAN had been removed again at 09:16:33, inside its window (ended 09:17:20) | Validation watches the symptom; older records derive it from their arguments | `test_add_vlan_validates_the_vlan_itself_not_device_health` fails on the old code. Live proof: `act-20261024-acdfc3` → `recurred` when the VLAN was removed during its window |

The record for #8 is left as it is: actions and audit rows are history, and editing them would
break the audit hash chain. This page is the correction.

## Defects found by the red team (build step 6)

Found by `tests/redteam/test_tester_redteam.py` (in-process, fakes, no lab) before any tester had a
pass. Each test fails on the code before its fix.

| # | Date | Defect | Impact | Fix | Regression test |
|---|---|---|---|---|---|
| 9 | 2026-10-24 | `python -m shimpaku.api` served `auth_mode=off` on any bind | Every caller on `0.0.0.0` would be the operator (approve, tokens, invites); only `/mcp` refused | Non-loopback bind refused in off mode (`21b795c`) | `test_auth_off_rest_refuses_a_non_loopback_bind` |
| 10 | 2026-10-24 | Ask Shim gave every non-tester asker an `agent` caller | A `read` token could have Shim propose actions (scope escalation; approval still human) | A principal that cannot propose gets a `visitor` Shim (`2fad8b1`) | `test_read_token_cannot_propose_through_shim` |
| 11 | 2026-10-24 | No cap on REST body size | A tester could store megabytes of `args` per proposal in action and audit rows | 413 over 64 KiB before parsing (`f528c44`) | `test_huge_bodies_are_refused_before_parsing` |
| 12 | 2026-10-24 | Demo operation errors returned the raw exception text | A device's management address reached the tester and the audit rows | Addresses replaced with `<host>`; full text only in the daemon log (`4743650`) | `test_device_errors_do_not_leak_management_addresses_to_a_tester` |
| 13 | 2026-10-24 | MCP `inventory_list` returned `mgmt_ip` to tester callers | A tester's token or Shim listed every management address | Dropped for tester and visitor callers (`c00c863`) | `test_mcp_inventory_hides_management_addresses_from_a_tester` |

## How a reviewer can check

```bash
.venv/bin/pytest -q                                   # 503 passed, 2 skipped (live-lab tests need -m lab)
git log --oneline                                     # every fix is its own commit, with evidence in the message
curl -s 127.0.0.1:8780/api/actions/act-20261024-9b7065   # the false verification, unaltered
curl -s 127.0.0.1:8780/api/actions/act-20261024-acdfc3   # the recurrence, caught
```
