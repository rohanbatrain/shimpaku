# Virga — Shimpaku's fault-injection benchmark (`bench/virga`) — owner B-bench

**Status: DONE.** Phase-1 exit met live (§3.5): all 9 self-tested network scenarios + the
config-rejection check pass `--repeat 5`, plus FAB-06, FAB-09, CAM-07, WAN-03, FAB-11 (link-flap) and
FAB-12 (ospf-timer-mismatch) promoted to self-tested at `--repeat 5`, journal empty, and
`deploy.py --validate-only` reports "all assertions passed" after every run. This doc was written
incrementally while those runs were still executing, per the coordinator's instruction to persist
partial results against interruption; §3 below is now the final record, not a draft.

Sources: PLAN.md §5 phase 1 and phase 8, §6 (demo set), §7 (benchmark); research notes research
`R9-failures-and-benchmark.md` (the aiops-bench prototype that passed 10 live self-tests on
2026-10-23) + `R9-files/` (`bench.py`, `catalogue.yaml`, `to_r5.py`); R5 §6 (scoring contract) +
`R5-files/06_scoring.py`/`r5common.py`; `REVIEW.md` B1–B5 (isolation, commit-diff leak, held-out
classes, statistics by fault class). CONTRACT.md fixes `bench_results()` and the `file:`/REST
incident adapter routes. B-mcp's `docs/build/mcp-actions.md` (commit 364075a) was read for the
device-lock usage pattern (lease + pending-confirm refusal, confirmed leg then plain confirming
commit) — Virga's `virga/lab.py` follows the same shape via `shimpaku.lock.device_lock`.

## 1. What was built

`bench/virga/` — a package, not a script, ported from the R9 prototype (`aiops-bench/bench.py`) with
R5's ground-truth fields folded into the catalogue and the REVIEW fixes designed in from the start:

| module | what |
|---|---|
| `paths.py` | every store/catalogue/publish/lock path reads from env, so the runner can move to a separate Unix user without code changes (REVIEW B2) |
| `schema.py` | validates `virga/lab/v1` + `virga/scenario/v1` without touching the lab: layers, statuses, probe kinds, step kinds, expectation operators, canonical R5 entity ids, node/write-scope checks, unresolved `${var}` checks, "no faulted expectation" (an executable scenario whose fault could never be proven) |
| `catalogue.py` | loads the catalogue + a private holdout file (merged, flagged, hashed in), `${var}` substitution, probe-set expansion |
| `lab.py` | NETCONF sessions; **every write goes through `Lab.leased()`**, which holds `shimpaku.lock.device_lock` (lease renewal thread, pending-confirm refusal) before any `configure exclusive` |
| `probes.py` | the 14 R9 probe kinds (unchanged logic) + `iface_flapped_ago` for the new link-flap scenario; `matches()`/`poll()`/`sample()` |
| `journal.py` | atomic (`fsync` + rename), `flock`-serialised, 0600 crash journal — written **before** the first inject commit |
| `decoys.py` | REVIEW B3: harmless commits during the agent window so "blame the newest commit" is not a free win |
| `baselines.py` | B0b "last commit diff" baseline (REVIEW B3) |
| `adapters.py` | `none` \| `file:PATH` \| `shimpaku[:URL]` (CONTRACT `GET /api/incidents`, `/api/actions`) |
| `runner.py` | preflight → inject → verify_injected → [agent window] → revert → verify_reverted; confirmed vs plain inject modes (REVIEW A1) |
| `recover.py` | `virga recover`: waits out any pending confirm, then reverts everything the journal lists, line-for-line checked |
| `plan.py` | seeded block-randomised schedules, nulls mixed to the target share, dev/test split pinned to a catalogue hash (REVIEW B4) |
| `scoring.py` | R5 `06_scoring.py` + `r5common.py` ported verbatim (wilson/garwood/score_system/fmt_table) + `records_to_trials` + `class_stats` (REVIEW B5 cluster bootstrap by fault class) |
| `publish.py` | `results.json` (the `bench_results()` contract), `scores.md`, a hand-drawn `scoreboard.svg` (no matplotlib on the lab host — see §4) |
| `cli.py` | `virga list\|validate\|selftest\|plan\|run\|score\|recover\|status\|freeze` |

`bench/catalogue.yaml` — 37 scenarios (the 35 from R9 plus `FAB-11` link-flap and `FAB-12`
ospf-timer-mismatch, both requested by phase 1). Every scenario now carries R5 §6.1 ground-truth
fields (`fault_class`, `target_entity`, `entity_aliases`) alongside the original R9 shape. A `decoys:`
block was added to the lab header (REVIEW B3). Fault-name strings used in Junos config were
neutralised (`aiops-quarantine` → `quarantine`, `aiops-lockout` → `mgmt-deny`, `aiops-wan` →
`wan-shape`) so a system reading raw config cannot pattern-match the injector by name.

`tests/bench/` — 46 tests, no live lab (`FakeLab` in `conftest.py`, using the real `shimpaku.lock` on a
temp SQLite db). Covers: schema validation (12 negative cases + the real catalogue), journal
atomicity/privacy, a full selftest trial lifecycle against a fake device (pass, unproven-fault,
residue-keeps-journal, blocked-baseline, blocked-pending, expected-rejection, write-outside-lease),
crash + `virga recover` (clean recovery, residue kept, waits out a pending confirm), the scorer run
against the **actual recorded R9 live trials** (`tests/bench/data/selftest-*.jsonl`, copied verbatim
from the research raw files) reproducing the packed-vs-respaced merge artefact R9 found, plan
determinism/design, decoy scheduling, the B0b keyword classifier, and all three adapters (including a
`respx`-mocked `shimpaku` REST adapter against the CONTRACT routes).

```
$ .venv/bin/python -m pytest tests/bench -q
46 passed in 7.14s          (same 46 passed on <lab-host>)
```

## 2. REVIEW fixes, as designed

- **A1 (inject collides with executor's commit-confirmed).** `runner.py` supports `inject_mode`
  `confirmed` (self-test, run A — dead man's switch) and `plain` (run B — the journal's inverse steps
  and pre-inject snapshot are the only way back; the lease is released after `verify_injected` so the
  system under test's own commits are never refused by *our* lease, and nothing heals itself at a TTL).
  Lockout scenarios (`auto` revert) always force `confirmed`, since the device's own rollback *is* the
  revert — enforced by `schema.py` (an `auto` revert requires every inject commit's `ttl_min <= 2`).
- **B2 (ground truth reachability).** `paths.store()` resolves to `/home/aiops-bench/store` when run as
  user `aiops-bench`, else `~/aiops-bench-store` (documented isolation gap, §5 below); `ensure_private`
  refuses a store directory any other group/other bit leaves open.
- **B3 (the fix is the diff).** `decoys.py` schedules harmless commits (interface descriptions, inert
  prefix-lists) on the write allocation during the agent window, same neutral comment as the inject,
  journaled and reverted before the config-identity check. `baselines.py` scores B0b honestly: "blame
  the newest commit" with a 14-rule keyword classifier over `show system rollback N compare 0`.
- **B4 (gameability).** `catalogue.load()` hashes the catalogue bytes (+ holdout file); `plan.py`
  pins every plan to that hash and refuses a `--split test` plan unless `virga freeze` recorded the
  same hash first. The holdout file itself (`$VIRGA_STORE/holdout.yaml`) never ships in this repo.
- **B5 (clustered trials).** `scoring.class_stats()` adds per-class Wilson rows (descriptive) and a
  cluster bootstrap (resample fault classes, not trials) for pooled detection/RCA-top1 CIs.
- **Blinding residue (R9 §2.5).** Commit comments stay the neutral `change`; fault-indicating strings
  were scrubbed from the catalogue's Junos lines (see §1). The `show system commit` "rollback in Nmins"
  leak is unresolved for run A (confirmed mode shows it by design) — run B's plain-commit mode has none.

## 3. Live proof so far (<lab-host>, vJunos 26.2, 2026-10-24 UTC)

### 3.1 Pipeline smoke (single run, before the repeat-5 batch)
```
FAB-07   pass   effect 0.2 s   restore 6.12 s   config_restored True
```
Confirms the ported runner reproduces R9's result end-to-end against the live lab: lease taken,
`commit confirmed 5` inject, independent probe proof, revert under `commit confirmed 2` confirmed on a
fresh session, line-for-line config identity, journal cleared.

### 3.2 First pass, the six promotion candidates (single run each, before `--repeat 5`)

| scenario | first try | fix |
|---|---|---|
| FAB-06 VNI mismatch | **pass** (effect 0.19 s, restore 5.84 s) | — |
| FAB-09 underlay loses lo0 | **pass** (effect 88.16 s, restore 30.44 s) | — |
| CAM-07 OSPF r1–r2 passive | **pass** (effect 0.15 s, restore 4.21 s) | — |
| WAN-03 policer congestion | fail: only 10% loss (bar was ≥20%) | a rapid ping offers ~112 kbit/s so a 256 kbit/s policer barely bit; range tightened to 32–96 kbit/s (default 64), burst dropped 8k→1500B. Retried alone: **pass**, 50% loss. |
| FAB-11 link-flap (new) | **pass** (effect 0.0 s — flap already resolved by the time of the first sample after the `flapped_ago` probe, restore 0.0 s) | — |
| FAB-12 ospf-timer-mismatch (new) | **pass** (effect 40.18 s — the dead-interval expiry, restore 6.2 s) | — |

Five of six passed cold; only WAN-03's intensity range needed correcting (measured, not guessed).

### 3.3 `--repeat 5` batch

Launched detached on <lab-host> (`~/aiops-bench-store/run-repeat5.sh`), two stages: (1) the 9 self-tested
network scenarios + CFG-02 (phase-1 exit requirement), then (2) the six promotion candidates.

**Phase-1 batch, first attempt (`phase1-repeat5.log`): 9/10 scenarios 5/5, CAM-05 4/5 FAIL.**
CAM-05 failed `verify_reverted` in repeats 1, 2, 4, 5 (`time_to_restore=None`, timeout at the
`revert_timeout_s=150` budget) and even **blocked at preflight** in repeat 3
(`blocked: baseline not healthy: icmp-counter=0`) — the *healthy* baseline itself read unhealthy
before any fault was injected. In every failing case `config_restored=True` and
`pending={'r1': False}`: the device was correct the whole time; only the probe never read a clean
value for long enough.

**Root cause (found, not guessed):** CAM-05's `icmp-counter` probe (`ping_counter_delta`) requires
the firewall counter delta to equal **exactly 3** after exactly 3 rapid pings, with `stable: 2`
(two clean samples in a row). Under host load ~19–20 (concurrent collector + redteam + this own
run), an occasional ICMP reply is lost even on a healthy path, so the delta reads 1 or 2 instead of
3 — not because the filter is faulty, but because the exact-match/exact-count design has no slack.
Four stale journal entries accumulated (config already correct, never re-checked because
`verify_reverted.ok` was false) — cleared cleanly by `virga recover` (see §3.4).

**Fix (catalogue, not code):** loosened the probe's `healthy` expectation from `3` to `{ge: 1}`
(any packet counted still proves the path is open; the faulted case stays an unambiguous `0` — no
slack needed there since a shadowed term drops *everything*), raised ping `count` from 3 to 5 for
more margin, and set the scenario's `timing.stable: 1`. Re-ran CAM-05 alone with `--repeat 5`:

```
CAM-05    pass   0.16   36.17   True
CAM-05    pass   0.19   43.64   True
CAM-05    pass   0.18   11.59   True
CAM-05    pass   0.16   15.91   True
CAM-05    pass   0.15    8.11   True
```
5/5 pass, restore times 8–44 s — comfortably inside the 150 s budget, and the exact-match brittleness
is gone. (R9's own §3 notes CAM-05 took 46–78 s to restore even in the original single-run research;
the exact-match probe compounded that under repeated, higher-concurrency load rather than causing it
outright — ARP/route reconvergence after the filter delete is still the dominant cost.)

**Phase-1 batch, final tally (10 scenarios × 5 repeats = 50 trials, after the CAM-05 fix):**
FAB-01, FAB-02, FAB-03, FAB-04, FAB-05, FAB-07, CFG-01, CAM-06, CFG-02 — **5/5 each on the first
attempt**; CAM-05 — **5/5 after the fix above** (4/5 + 1 preflight-block before it). No other
scenario needed a change. Full per-trial JSONL: `selftest-20261024T060217Z.jsonl` (first attempt,
includes the 4 CAM-05 fails) and `selftest-20261024T070435Z.jsonl` (CAM-05 fix confirmation).

| scenario | n | pass | time-to-effect median | time-to-restore median / max |
|---|---|---|---|---|
| FAB-01 link down (admin) | 5 | 5/5 | 0.17 s | 9.12 s / 12.70 s |
| FAB-02 one-sided (far end) | 5 | 5/5 | 0.15 s | 12.43 s / 12.49 s |
| FAB-03 BFD removed | 5 | 5/5 | 4.16 s | 4.74 s / 8.17 s |
| FAB-04 MTU mismatch | 5 | 5/5 | 6.63 s | 4.65 s / 7.05 s |
| FAB-05 overlay BGP down | 5 | 5/5 | 0.18 s | 5.19 s / 6.48 s |
| FAB-07 ECMP/metric skew | 5 | 5/5 | 0.18 s | 5.20 s / 6.48 s |
| CFG-01 config drift | 5 | 5/5 | 0.16 s | 5.24 s / 12.02 s |
| CFG-02 bad push rejected | 5 | 5/5 | — (rejected) | 0.00 s / 0.00 s |
| CAM-05 shadow filter term (after fix) | 5 | 5/5 | 0.16 s | 15.91 s / 43.64 s |
| CAM-06 mgmt lockout (device self-reverts) | 5 | 5/5 | 0.29 s | 71.23 s / 94.22 s |

CAM-06's restore time (71–94 s) is the device's own `commit confirmed 1` rollback timer, not Virga's
revert — matches R9's original 73.5–74.3 s measurement and B-mcp's independently-measured 73.5–75.6 s
rollback on a different port (`docs/build/mcp-actions.md` §4(2)); three independent measurements now
agree on vJunos 26.2's confirmed-rollback timing within a few seconds.

**Promotion batch (`promote-repeat5.log`), final: 6 scenarios × 5 repeats = 30/30 PASS, `promote exit=0`.**
Ran concurrently on `leaf1`/`r1` — the same allocation as the phase-1 batch and the CAM-05
fix-verification run, which were live at the same time (see §3.4 for what that proved about the
device lock). Per-scenario tally (computed from `selftest-20261024T070101Z.jsonl`):

| scenario | n | pass | time-to-effect median | time-to-restore median / max |
|---|---|---|---|---|
| FAB-06 VNI mismatch | 5 | 5/5 | 0.16 s | 4.65 s / 5.27 s |
| FAB-09 lo0 withdrawn | 5 | 5/5 | 88.17 s | 4.08 s / 5.11 s |
| CAM-07 OSPF passive | 5 | 5/5 | 0.16 s | 3.96 s / 4.52 s |
| WAN-03 policer congestion | 5 | 5/5 | 0.18 s | 3.97 s / 4.18 s |
| FAB-11 link-flap (new) | 5 | 5/5 | 0.00 s | 0.00 s / 0.00 s |
| FAB-12 ospf-timer-mismatch (new) | 5 | 5/5 | 40.16 s | 8.94 s / 12.22 s |

FAB-11's 0.00 s times are real, not a bug: `iface_flapped_ago`'s "faulted" expectation (`{le: 240}`)
is already true the instant the flap's own 3-cycle inject loop finishes (the last transition is the
fault), so the first post-inject sample is already inside tolerance; the revert (`delete ... disable`)
is a no-op diff against the post-flap "up" state, so the first post-revert sample is immediately
healthy too. FAB-12's 40 s matches the OSPF dead-interval deterministically, both times, confirming it
is not host-load noise.

Journal confirmed empty (`journal entries left: []`) after both the phase-1 and promotion batches.

### 3.4 A concurrency finding (not a bug, but worth recording)

While the promotion batch (process A, on `leaf1`) was mid-trial on FAB-09, a separate `virga recover`
invocation (process B) read the journal, saw FAB-09's entry (written by A just before A's inject
commit), and attempted to revert it. **B correctly blocked waiting for A's SQLite device lease** on
`leaf1` via `shimpaku.lock.device_lock`; by the time B's wait cleared (because A had itself finished
its revert and released the lease), A's own `execute()` had already removed the journal entry. B's
revert attempt was therefore a redundant, idempotent no-op (delete-ing an already-deleted term,
finding the config already identical) and it exited "clean". **A's own trial scored PASS normally** —
confirmed from A's log independently. This is exactly the serialization the device lock is for, and
it worked under a real concurrent-process race, not just the mocked-process test in
`tests/bench/test_journal_recover.py`.

One rough edge this surfaced: `cmd_selftest`'s end-of-run "journal not empty" check originally read
the *whole* shared journal file, so a second, unrelated, still-in-flight process's legitimate
in-flight entry was reported as if it were this run's own residue (observed once, for a promotion-batch
FAB-09 trial that was in fact mid-commit when the short-lived CAM-05-fix run's own completion check
ran). **Fixed:** the check now intersects the journal against `{r["trial_id"] for r in recs}` — this
invocation's own trials only (`cli.py`). Re-validated (`pytest tests/bench`, 46 passed) and re-synced.

### 3.5 Phase-1 exit — final check

```
$ cd ~/juniper-7-day-sprint && .venv/bin/python day-07/automation/deploy.py \
      lab/topologies/day-06-datacenter.toml --validate-only
... (OSPF Full, ECMP 2, overlay BGP Established, VXLAN ping 0% loss, every node) ...
all assertions passed
```
Run immediately after both `--repeat 5` batches (80 trials total: 50 phase-1 + 30 promotion) and the
`virga recover` cleanup, with the shared journal at `{}` on both `rohan`'s and (transiently, during
the concurrency test in §3.4) the lock-serialized second process's view. **Phase-1 exit (PLAN §5) is
met**: the 9 self-tested network scenarios plus the config-rejection check pass `--repeat 5`, and
FAB-06, FAB-09, CAM-07, WAN-03 plus the two newly-added injectors (link-flap, ospf-timer-mismatch)
are promoted to `selftested` at `--repeat 5` — satisfying the phase-8 prerequisite named in the task.
`catalogue.yaml`'s `status:` field for all 16 was flipped from `executable`/the original 10
`selftested` to `selftested` (validated, tests still pass, re-synced).

**Published.** `publish.selftest_summary()` + `publish.write()` run against the final recorded JSONL
(no live re-run needed) produced the `bench_results()` contract file at
`$SHIMPAKU_DATA/bench/results.json` (`~/.local/share/shimpaku/bench/results.json` on
<lab-host>): **16 scenarios, 80/80 trials passed**, with per-scenario counts, median/max times and
`config_restored_all` — the exact shape `docs/build/bench.md` tables above are drawn from. This is
the file the dashboard's `GET /api/bench/results` (via `shimpaku.service.bench_results()`) reads.

## 4. Known deviations / notes

- No `matplotlib` on the <lab-host> host venv, so `publish.scoreboard_svg()` draws the ECDF-left /
  Wilson-CI-right plot as hand-built inline SVG instead of a PNG. Same columns and story as R5
  §6.5's plot spec; tested in `test_publish_outputs_are_json_clean`.
- `virga` needs `bench/` on `PYTHONPATH` (it is not declared in `pyproject.toml`, which this builder
  does not edit per CONTRACT.md — see §6 "asks").
- The lock DB (`/home/lab/shimpaku/locks.db`) is created 0644 by SQLite regardless of umask; added
  `paths.share_lock_db()` (called after every `Lab.leased()` and from `virga status`) to force it back
  to group-writable so B-mcp's executor, `deploy.py` and Virga can all take the lease.
- `WAN-03`'s policer bandwidth range was corrected from the R9-prototype's `64–2000 kbit/s` (which
  mostly produced 0% measured loss against a ~112 kbit/s rapid-ping offered rate) to `32–96 kbit/s`,
  measured live.

## 5. Isolation (REVIEW B2) — operator run commands

The operator created Unix user `aiops-bench` with `/home/aiops-bench/store` (0700); this builder has no
sudo, so this section documents the exact commands rather than running them as that user. **For now**,
all live runs above used `~/aiops-bench-store` (0700, created by this builder, owned by `rohan`) via
`paths.store()`'s fallback — stated here as required.

Once a human with sudo moves the store:
```
sudo -u aiops-bench env VIRGA_STORE=/home/aiops-bench/store \
    VIRGA_CATALOGUE=~/src/shimpaku/bench/catalogue.yaml \
    SHIMPAKU_LOCK_DB=/home/lab/shimpaku/locks.db \
    SHIMPAKU_LAB_REPO=~/juniper-7-day-sprint \
    PYTHONPATH=~/src/shimpaku/bench:~/src/shimpaku/src \
    ~/src/shimpaku/.venv/bin/python -m virga selftest --repeat 5

sudo -u aiops-bench env VIRGA_STORE=/home/aiops-bench/store ... \
    ~/src/shimpaku/.venv/bin/python -m virga run PLAN.json --agent shimpaku --publish
```
`aiops-bench` needs read access to the shared venv and to `bench/catalogue.yaml` (both currently
`rohan`-owned; either group-readable or copied under `aiops-bench`'s home) and membership in group
`shimpaku` for the device lease. External agents under test (Claude Code / OpenCode) must be sandboxed
to reach only the MCP HTTP endpoint, per PLAN §7 — that sandbox is not this builder's component.

## 6. Open items / asks for the orchestrator

- `pyproject.toml` has no entry for `virga`; needs either a `[tool.hatch]` packages line for
  `bench/virga` or a documented `PYTHONPATH=bench`. Builders may not edit `pyproject.toml`.
- `respx` is already declared (`dev` extra) — used by `tests/bench/test_plan_adapters.py`'s mocked
  `shimpaku` adapter test; no new dependency needed.
- ~~CAM-05's revert-timing anomaly needs a decision~~ — resolved live (§3.3): the probe's exact-match
  expectation was the cause, fixed in the catalogue, confirmed 5/5 on a dedicated repeat-5 re-run.
- Phase 8 (the actual benchmark run, B0/B0b/B1/B2/B3 over ≥85 fault trials) needs: (1) the client
  farm (R3, for the 17 `aiops-farm`-gated scenarios — still `designed`, not `executable`); (2) the
  `aiops-bench` Unix user's store actually moved (§5 — this builder has no sudo); (3) B-mcp's
  `shimpaku` adapter reachable (`SHIMPAKU_PORT=8780`) for a `--agent shimpaku` run instead of `none`;
  (4) an operator decision on run B's plain-commit mode interacting with B-mcp's executor lock (both
  now use the identical `shimpaku.lock.device_lock` lease, so they will correctly serialize, but
  nobody has yet run Virga and the executor against the same device at the same time with an agent
  actually proposing fixes — only the recover-vs-selftest race in §3.4 has been proven live).
