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

## How a reviewer can check

```bash
.venv/bin/pytest -q                                   # 256 passed, 2 skipped (live-lab tests need -m lab)
git log --oneline                                     # every fix is its own commit, with evidence in the message
curl -s 127.0.0.1:8780/api/actions/act-20261024-9b7065   # the false verification, unaltered
curl -s 127.0.0.1:8780/api/actions/act-20261024-acdfc3   # the recurrence, caught
```
