# Virga live — the operator's fault lab, reflected everywhere

**Status: design (2026-10-25), the contract the build follows.** Owner: the operator.

Virga (`bench/virga`) has so far run only as a CLI on the lab host (self-test 80/80, `docs/build/bench.md`).
This makes it the thing a viewer can watch: the operator picks a catalogued fault, Virga injects it into
the real devices, and the fault shows up wherever the system is watched — the dashboard's Topology,
Service levels, Events, Incidents and Theatre, the landing page's live strip, and the public status page —
then Virga reverts it and proves the revert.

Nothing is simulated. The fault is a real Junos commit made by the same runner, through the same lease
and journal, as the self-test. The rest of the system finds out the way it would find out about any
fault: telemetry, probes, SLEs, incidents.

## 1. Decisions

| # | Decision | Why |
|---|---|---|
| D1 | The daemon runs Virga in-process (`shimpaku/virga_live.py`), in a worker thread, **one trial at a time**, using `virga.runner.execute` unchanged in shape plus a `demo` mode. | One runner, one journal, one lease: a demo trial and a CLI trial cannot both write. `virga recover` from the CLI cleans up after a daemon that died mid-trial. |
| D2 | Only `Principal.can_present` may inject, revert or recover: the operator's own dashboard session (password + TOTP). Never a token, a tester or a visitor. | It is a write path into real devices on the public site. |
| D3 | Everyone (visitors too) may **watch**: catalogue, live trial state and history are read routes, built from an allow-list (no config lines, no management addresses, actor reduced to role). | The point is that a viewer sees the fault happen. |
| D4 | `demo` mode = self-test plus a **hold**: after the fault is verified, it stays in until the operator presses *Revert now* or `hold_s` runs out (default 180 s, max 600 s), then the normal revert + verify-reverted. | Self-test reverts within seconds; a viewer needs time to watch detection happen. |
| D5 | Every demo inject is `commit confirmed` with TTL ≥ `ceil(hold_s / 60) + 3` minutes. | If the daemon dies mid-hold, the device rolls itself back. The journal is the second way back. |
| D6 | Demo set = catalogue scenarios that are `executable`, self-tested, not holdout, and not in `DEMO_EXCLUDE = {"CAM-06"}` (management lockout blinds the daemon's own NETCONF and telemetry). Latent scenarios (`FAB-03`, `CFG-02`) stay in, labelled "no user impact". | Only faults with a proven inject *and* revert are offered live. |
| D7 | Off unless `SHIMPAKU_VIRGA_LIVE=1` on the lab. Replay: always off ("the lab is offline"). Canned demo service: a simulated trial (`api/demo_virga.py`), labelled simulated, for dashboard development and screenshots only. | The public replay must never pretend to inject. |
| D8 | Every inject, revert, recover — allowed or refused — is one hash-chained audit row: `virga_inject`, `virga_revert`, `virga_recover`, with scenario, nodes, outcome, role. Theatre's "Fault injected" stage also accepts `virga_inject` rows. | Same audit rule as every other write. |
| D9 | If the journal holds an unfinished trial at daemon start, state is `needs_recovery`, injects are refused (409) until the operator presses *Recover* (runs `virga.recover`). | A half-reverted lab must not take a second fault. |
| D10 | Rate: category `virga`, 6 injects / hour per principal; Caddy zone `virga` 10 / 10 min per client IP on `POST /api/virga/*`. | The lab host overheats; a demo is one fault at a time. |

## 2. API (the contract the frontend and the status page code against)

All JSON. `phase` ∈ `preflight · inject · verify_injected · hold · revert · verify_reverted · done`.
`status` ∈ `pending · running · ok · failed · skipped`.

```
GET  /api/virga/catalogue          reader
  { enabled: bool, reason: str|null, simulated: bool,
    hold: { default_s: 180, max_s: 600 },
    scenarios: [ { id, title, layer, site: "campus"|"fabric", nodes: [str], links: [[a,b]],
                   impact: "user"|"latent", demo_ok: bool, why_not: str|null,
                   est_s: int, selftest: { passes: int, runs: int, last_at: float|null } } ] }

GET  /api/virga/state              reader     (also the payload of every SSE "state" event)
  { enabled, reason, simulated, busy: bool, needs_recovery: bool,
    trial: null | { id, scenario, title, layer, site, nodes, links, by_role,
                    t_start: float, hold_until: float|null, phase, verdict: null|"pass"|"fail",
                    phases: [ { name, status, t_start, t_end, detail: str } ],
                    log: [ { t: float, msg: str } ]  (last 50, scrubbed) },
    recent: [ { id, scenario, title, t_start, t_end, verdict, time_to_effect_s, time_to_restore_s,
                incident_opened: bool } ]  (last 10) }

GET  /api/virga/stream             reader     SSE: event "state" on every change, ping every 15 s
GET  /api/virga/trials             reader     { trials: [ same shape as recent ], selftest: {...} }
POST /api/virga/trials             can_present  body { scenario: str, hold_s?: int }
       202 { trial_id }   403 not operator · 409 busy | needs_recovery | lease held · 422 not in demo set
POST /api/virga/trials/{id}/revert can_present  202 {ok: true}   (ends the hold now)
POST /api/virga/recover            can_present  200 { recovered: [trial ids], ok: bool }

GET  /api/public/summary           (existing) gains
  drill: { active: bool, scenario: str|null, title: str|null, phase: str|null, since: float|null, site: str|null }
GET  /api/public/health            anonymous, cached 10 s, rate-limited (category read)
  { campus: "healthy"|"degraded"|"offline", fabric: "healthy"|"degraded"|"offline",
    drill: "idle"|"active", lab: "live"|"replay" }
```

`/api/public/health` is for Uptime Kuma keyword monitors; values are bare words so a keyword match is
unambiguous. "degraded" = the site has an open incident, or a drill on the site has a verified fault in place
(phase hold / revert), or a device read fails. (First built as "latest SLE minute below goal"; live, that flapped
red every few minutes on Resources·CPU minutes from the overloaded lab host, which the incident engine already
filters by persistence, so the status page now follows the incident engine.)

## 3. Where it shows

| Surface | What a viewer sees |
|---|---|
| `/virga` (new page, nav "Fault lab") | Catalogue by site and layer; the live trial as a phase timeline with a hold countdown and the runner's log; history. Operator: *Inject*, hold slider, *Revert now*, *Recover*. Visitor: the same page read-only with "Sign in as operator to inject". |
| Every page | A drill banner while a trial is live: "Fault drill running: FAB-01 on leaf1 — 0:42. Watch: Topology · Theatre · Fault lab". |
| Topology | Nodes and links the trial touches get a "fault injected" mark. |
| Overview | A Virga tile: live state, last drill and its verdict. |
| Benchmark | Names Virga; links to `/virga`. |
| Landing page | Live strip says "fault drill running" during a trial. |
| Status page (Kuma) | Group "Live lab": *Campus network*, *Fabric network*, *No fault drill running* — keyword monitors on `/api/public/health`, 20 s interval, so an injected fault turns them red within a minute and green after the revert. |

## 4. Out of scope now

Scored benchmark runs from the dashboard (`plan → run → score` stays a CLI job), non-Junos scenarios,
multiple concurrent trials, tester access to injection.

## 5. Built (backend, 2026-10-25)

| File | What |
|---|---|
| `bench/virga/runner.py` | `Context.mode = "demo"`: `hold_s`, `revert_now` (a `threading.Event`), `on_event` (phase start/end, every log line, the verdict). Hold after `verify_injected` OK, sampling the fault probes every `poll_s` (`t_fix_verified` when something else fixed it). D5: every demo inject is `commit confirmed`, TTL `max(catalogue, ceil(hold_s/60)+3)`, also written to the journal so `virga recover` waits long enough. Self-test and bench paths unchanged. |
| `src/shimpaku/virga_live.py` | The manager (D1, D6, D9): one trial at a time in a worker thread, holder `virga:live:<role>`, the CLI's journal (`$VIRGA_STORE/journal.json`), private full records in `$VIRGA_STORE/live/live-YYYYMMDD.jsonl`, public summaries in `$VIRGA_PUBLISH/virga-live.json`, the catalogue view, `ReplayVirga`. |
| `src/shimpaku/api/routes_virga.py` | The §2 routes, mounted in `app.py` with one line. |
| `src/shimpaku/api/demo_virga.py` | The canned simulated trial (`simulated: true`, about 60 s, hold capped at 35 s). |
| `src/shimpaku/api/routes_public.py` | `drill` block on `/api/public/summary` (never from its 20 s cache); `/api/public/health`. |
| `src/shimpaku/api/routes_theatre.py` | `break_rows` accepts `virga_inject` rows with status `changed`. |
| `src/shimpaku/snapshot_cli.py`, `api/replay` | The snapshot records `/api/virga/trials` and `/api/virga/catalogue`; the replay serves them with `enabled: false`. |
| `src/shimpaku/config.py`, `daemon.py` | `SHIMPAKU_VIRGA_LIVE` (D7); the daemon loads the catalogue at start when it is on. |
| `deploy/vps/Caddyfile` | Zone `virga`: 10 POSTs / 10 min per client IP on `/api/virga/*` (D10). |
| `pyproject.toml` | Console script `virga = "virga.cli:main"`. |

Details the contract left open, as built:

- `phases` lists the six working phases; `done` appears only as the trial's `phase` once it has ended.
  A trial blocked at preflight has `verdict: "fail"`, preflight `failed`, the rest `skipped`.
- A finished trial stays in `state.trial` for 10 minutes, then `trial` is `null`; `recent` keeps it.
- `est_s` is the trial length **without** the hold (self-test medians when published).
- `impact` is `latent` for FAB-03 and CFG-02 (a scenario `impact:` field would override); CTRL-00 and
  held-out scenarios are never listed. `hold_s` outside 30–600 s is 422.
- `needs_recovery` is true whenever the journal holds an entry and no trial is running (ours, or a
  crashed CLI's). `409` codes: `busy`, `needs_recovery`, `lease_held` (another holder on a node).
- Rate: `virga` counts every inject attempt, 6/h per principal and 12/h overall; in
  `SHIMPAKU_RATE_LIMITS`, `virga=a/b` is read **per hour**. Revert and recover spend `write`.
- Audit: refusals are written by the route (`refused:<code>`); an accepted inject's row is written
  when its inject phase ends, status `changed`, `no_change`, `rejected`, `blocked` or `error:<type>`.
- `POST /api/virga/recover` waits at most 60 s for a pending `commit confirmed` (the edge gives a
  write 100 s); a node still pending is left to roll itself back and the answer is `ok: false`:
  press *Recover* again after the TTL.
- `/api/public/health` uses stored reads only: `degraded` = the newest SLE minute below goal (100 %
  when none is set) or a device of the site with no stored health sample in 10 min; `offline` = every
  device of the site failing, or neither SLE nor device health readable.
