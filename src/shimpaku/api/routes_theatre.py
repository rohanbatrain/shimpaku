"""Theatre mode (``/api/theatre*``): one break-and-fix run of the demo fixture, as it happens.

A *run* is the most recent demo-fixture action (``add_vlan_to_trunk`` on ``sw1 ge-0/0/5`` VLAN
999, ``actions/fixture.py``) together with the fault that preceded it, laid out as ordered
stages: fault injected → probe dip → incident → proposed → guardrails → approved → commit
confirmed leg → fresh-session health check → confirm commit → validation window → verified or
recurred.

Honesty: every stage time is a stored timestamp: the ``demo_break`` audit row (or a Virga drill's
``virga_inject`` row that changed a device, docs/build/virga-live.md D8), the SLE minute,
the incident's ``opened_at``, the action's ``created`` / timeline / commit records and its
validation window. A stage that has no record is ``pending`` (not yet) or ``skipped`` (it did not
happen, e.g. no incident opened), never estimated. Nothing is simulated here; demo mode gets its
canned run from ``api/demo_theatre.py`` through the same service function names.

Guest safety: the payload is built from an allow-list: no management addresses, no device
config, no tokens. The caller is reduced to name / kind / role, executor notes are reduced to a
pass/fail flag, and error text goes through ``scrub_hosts``. The fixture port read is reduced
to one boolean (``routes_viz.fixture_state``), as on the Topology page.

Writes are not here: the page's Break / Propose / Approve buttons call the existing endpoints
(``/api/demo/break``, ``/api/assistant/ask`` or ``/api/actions``, ``/api/actions/{id}/approve``)
with their existing permission checks.

``app.py`` mounts it with ``app.include_router(routes_theatre.router(call, reader))``.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from shimpaku.api import view

RUN_TTL_S = 1.0        # one build serves every viewer for this long
FIXTURE_TTL_S = 5.0    # the fixture port is a device read: at most one per 5 s, whoever watches
STREAM_TICK_S = 1.5
BREAK_LOOKBACK_S = 2 * 3600   # a fault older than this before a proposal is not "its" fault
COMPLETED = {"verified", "recurred", "failed"}
ENDED = COMPLETED | {"blocked", "rejected", "cancelled"}

STAGES = [
    ("break", "Fault injected"),
    ("dip", "Probes / SLE dip"),
    ("incident", "Incident opened"),
    ("propose", "Fix proposed"),
    ("guardrails", "Guardrails checked"),
    ("approve", "Approved"),
    ("commit", "Commit confirmed leg"),
    ("health", "Fresh-session health check"),
    ("confirm", "Confirm commit"),
    ("validate", "Validation window"),
    ("outcome", "Verified"),
]


def _scrub(text: Any, n: int = 200) -> str:
    from shimpaku.service import scrub_hosts
    return scrub_hosts(str(text))[:n]


def _created(a: dict | None) -> float | None:
    return view.ts((a or {}).get("created")) or view.ts((a or {}).get("created_at"))


def is_fixture_action(a: dict) -> bool:
    from shimpaku.actions import fixture
    args = dict(a.get("args") or {}) if isinstance(a.get("args"), dict) else {}
    return fixture.is_fixture(a.get("kind"), args)


def _first(tl: list, to: str) -> float | None:
    return next((view.ts(t.get("ts")) for t in tl if t.get("to") == to and t.get("from") != to), None)


def _note(tl: list, prefix: str) -> dict | None:
    return next((t for t in tl if str(t.get("note") or "").startswith(prefix)), None)


BREAK_TOOLS = ("demo_break", "virga_inject")


def break_rows(audit: dict) -> list[dict]:
    """``demo_break`` and ``virga_inject`` audit rows that changed a device, newest first:
    ``{t, by_role, by_kind}`` plus, for a Virga drill, ``virga: {scenario, nodes}``."""
    out = []
    for r in (audit or {}).get("rows") or []:
        tool = (r.get("tool") or r.get("method")) if isinstance(r, dict) else None
        if tool not in BREAK_TOOLS:
            continue
        status = r.get("status") or r.get("result_status")
        if status != "changed":
            continue
        t = view.ts(r.get("ts"))
        if t is None:
            continue
        actor = r.get("actor") if isinstance(r.get("actor"), dict) else {}
        b = {"t": t, "by_role": actor.get("role"), "by_kind": actor.get("kind")}
        if tool == "virga_inject":
            args = r.get("args") if isinstance(r.get("args"), dict) else {}
            b["virga"] = {"scenario": _scrub(args.get("scenario") or "?", 16),
                          "nodes": [_scrub(n, 32) for n in args.get("nodes") or [] if isinstance(n, str)][:8]}
        out.append(b)
    return sorted(out, key=lambda b: -b["t"])


DEVICE_SLES = frozenset({"fabric_health", "device_health"})


def first_dip(sle: Any, start: float, end: float, skip: frozenset[str] = frozenset()) -> dict | None:
    """The first SLE minute in ``[start, end]`` below the SLE's goal (100 % when none is set).
    SLEs whose id is in ``skip`` are not looked at."""
    v = view.sle_summary(sle) if isinstance(sle, dict) else None
    if not isinstance(v, dict) or v.get("available") is False:
        return None
    best = None
    for s in v.get("sles") or []:
        if s.get("id") in skip:
            continue
        goal = s.get("goal_pct") if isinstance(s.get("goal_pct"), (int, float)) else 100
        for p in s.get("series") or []:
            t, val = view.ts(p.get("t")), p.get("value")
            if t is None or not isinstance(val, (int, float)) or not (start <= t <= end) or val >= goal:
                continue
            if best is None or t < best["t"]:
                best = {"t": t, "sle": s.get("name") or s.get("id"), "value": val, "n": p.get("n"), "goal": goal}
    return best


def _incident(incidents: Any, a: dict | None, start: float, end: float) -> dict | None:
    from shimpaku.actions import fixture
    rows = view.incident_list(incidents).get("incidents") or [] if isinstance(incidents, dict) and \
        incidents.get("available", True) is not False else []
    want = (a or {}).get("incident_id")
    for i in rows:
        if want and (i.get("id") == want or i.get("incident_id") == want):
            return i
    for i in sorted(rows, key=lambda i: view.ts(i.get("opened_at")) or 0):
        t = view.ts(i.get("opened_at"))
        title = str(i.get("title") or "")
        if t is not None and start <= t <= end and (fixture.DEVICE in re.split(r"[^A-Za-z0-9-]+", title)):
            return i
    return None


def _caller(a: dict) -> dict:
    c = a.get("caller") if isinstance(a.get("caller"), dict) else {}
    return {"name": _scrub(c.get("name") or "?", 60), "kind": c.get("kind"), "role": c.get("role")}


def build_run(a: dict | None, breaks: list[dict], prev_created: float | None, *, sle: Any = None,
              incidents: Any = None, fixture_state: dict | None = None, now: float | None = None,
              live: bool = True) -> dict:
    """The run payload. ``a`` is the raw fixture action (service shape) or None (fault, no
    proposal yet). ``prev_created`` bounds which fault belongs to this action."""
    now = time.time() if now is None else now
    created = _created(a)
    floor = max(prev_created or 0, (created or now) - BREAK_LOOKBACK_S)
    ceil = created if created is not None else now
    brk = next((b for b in breaks if floor < b["t"] <= ceil), None)
    tl = (a or {}).get("timeline") or []
    commits = (a or {}).get("commits") or []
    status = (a or {}).get("status")
    val = (a or {}).get("validation") if isinstance((a or {}).get("validation"), dict) else {}
    pol = (a or {}).get("policy") or {}
    st: dict[str, dict] = {k: {"id": k, "label": lbl, "t": None, "state": "pending", "detail": None}
                           for k, lbl in STAGES}

    def done(k: str, t: float | None, detail: str | None = None, **extra: Any) -> None:
        st[k].update(t=t, state="done" if t is not None else st[k]["state"], detail=detail, **extra)

    start = brk["t"] if brk else created
    end_t = _first(tl, status) if status in ENDED else None
    if brk and brk.get("virga"):
        v = brk["virga"]
        done("break", brk["t"], f"Virga fault drill {v['scenario']} on {', '.join(v['nodes']) or 'the lab'} "
                                f"(injected by {brk.get('by_role') or 'someone'})")
    elif brk:
        who = brk.get("by_role") or "someone"
        done("break", brk["t"], f"VLAN 999 removed from sw1 ge-0/0/5 (demo break by {who}"
                                f"{' over ' + brk['by_kind'] if brk.get('by_kind') else ''})")
    elif a is not None:
        st["break"].update(state="skipped", detail="No recorded demo break before this proposal")

    # probes / SLE dip and incident: only meaningful once there is a fault to look after
    if start is not None:
        # The VLAN demo breaks a trunk wired to nothing: device health (CPU, memory, reachability of
        # the box) cannot show it, so a device-health dip then is a coincidence, not the fault's
        # effect. A Virga drill does hit traffic and devices, so it keeps every SLE.
        virga = bool(brk and brk.get("virga"))
        dip = first_dip(sle, start, end_t or now, frozenset() if virga else DEVICE_SLES)
        if dip:
            done("dip", dip["t"], f"first campus minute below goal after the fault: {dip['sle']} {dip['value']:g}%"
                                  f"{' (n = ' + str(dip['n']) + ')' if dip.get('n') is not None else ''}")
        elif sle is None or (isinstance(sle, dict) and sle.get("available") is False):
            st["dip"].update(state="skipped", detail="Service levels not collected")
        elif a is not None:
            st["dip"].update(state="skipped", detail="No probe-minute below goal recorded in this run" + (
                "" if virga else " (expected: the demo trunk carries no user traffic)"))
        inc = _incident(incidents, a, start, end_t or now)
        if inc:
            done("incident", view.ts(inc.get("opened_at")), f"{inc.get('id') or inc.get('incident_id')}: "
                                                             f"{_scrub(inc.get('title') or '', 120)}")
        elif a is not None:
            st["incident"].update(state="skipped", detail="No incident opened for this fault" + (
                "" if brk and brk.get("virga") else " (expected: with no traffic on the demo trunk, no service level crossed its threshold)"))

    if a is not None:
        c = _caller(a)
        done("propose", created, f"add_vlan_to_trunk sw1 ge-0/0/5 vlan 999 · {a.get('id')}", by=c)
        reasons = pol.get("reasons") or []
        checks = []
        for g in pol.get("checked") or []:
            fail = next((r for r in reasons if str(r).startswith(f"{g} ")), None)
            checks.append({"id": g, "passed": fail is None,
                           "name": view._GUARDRAIL_NAMES.get(g, g), "detail": _scrub(fail) if fail else None})
        passed = sum(1 for x in checks if x["passed"])
        if status == "blocked":
            done("guardrails", created, f"blocked: {passed} of {len(checks)} passed", checks=checks)
            st["guardrails"]["state"] = "failed"
        elif checks:
            done("guardrails", created, f"{passed} of {len(checks)} passed", checks=checks)
        approved = _first(tl, "approved")
        if approved:
            done("approve", approved, None, by={"name": _scrub(view._approved_by(a) or "?", 60)})
        elif status == "automatic":
            done("approve", _first(tl, "automatic"), "self-driving policy: no human approval needed")
        leg = next((x for x in commits if x.get("confirm_min")), None)
        if leg:
            done("commit", view.ts(leg.get("ts")), f"commit confirmed {leg['confirm_min']} · "
                                                    f"{leg.get('seconds')} s",
                 junos_time=_scrub(leg.get("junos_index0_time") or "", 40) or None,
                 comment=_scrub(leg.get("comment") or "", 80))
        chk = _note(tl, "fresh-session health check")
        if chk:
            ok = "'ok': True" in str(chk.get("note"))
            done("health", view.ts(chk.get("ts")), "passed: device reachable, rollback timer pending, "
                                                    "health not worse" if ok else "failed: not confirming")
            if not ok:
                st["health"]["state"] = "failed"
        conf = next((x for x in commits if x.get("leg") == "confirm"), None)
        if conf:
            done("confirm", view.ts(conf.get("ts")), f"plain commit · {conf.get('seconds')} s · rollback "
                                                      f"timer cleared",
                 junos_time=_scrub(conf.get("junos_index0_time") or "", 40) or None)
        vstart, vend = view.ts(val.get("starts")), view.ts(val.get("ends"))
        vchecks = [x for x in val.get("checks") or [] if isinstance(x, dict)]
        if vstart:
            st["validate"].update(t=vstart, state="done" if status in ENDED else "current",
                                  starts=vstart, ends=vend,
                                  checks=len(vchecks), checks_ok=sum(1 for x in vchecks if x.get("ok")),
                                  detail=f"VLAN 999 watched on the trunk for {round((vend - vstart))} s"
                                  if vend else None)
        if status in ("verified", "recurred"):
            done("outcome", _first(tl, status), "healthy for the whole window" if status == "verified"
                 else "symptom came back during validation; triage re-opened")
            st["outcome"]["label"] = "Verified" if status == "verified" else "Recurred"
            if status == "recurred":
                st["outcome"]["state"] = "failed"
                again = next((b for b in breaks if vstart and vstart <= b["t"] <= (st["outcome"]["t"] or now)), None)
                if again:
                    st["outcome"]["detail"] += f" (fault re-injected by {again.get('by_role') or 'someone'})"
                    st["outcome"]["refault_t"] = again["t"]
        if status in ("failed", "rejected", "cancelled"):
            k = next((k for k, _ in STAGES[3:] if st[k]["state"] == "pending"), "outcome")
            st[k].update(state="failed", t=_first(tl, status),
                         detail=_scrub((a.get("error") or {}).get("message") if isinstance(a.get("error"), dict)
                                       else a.get("error") or status, 240))
            if k != "outcome":
                st["outcome"].update(label=status.capitalize(), state="failed", t=_first(tl, status))
    if status not in ENDED and not any(s["state"] == "current" for s in st.values()):
        cur = next((k for k, _ in STAGES if st[k]["state"] == "pending" and k not in ("dip", "incident")), None)
        if cur and (a is not None or st["break"]["state"] == "done"):
            st[cur]["state"] = "current"

    mode = "none" if a is None and brk is None else "pending" if a is None else (
        "replay" if status in ENDED else "live")
    fx = None
    if fixture_state is not None:
        fx = {"present": fixture_state.get("present"), "reason": fixture_state.get("reason"), "live": live}
    return {
        "mode": mode,
        "action": ({"id": a.get("id"), "status": status, "proposed_by": _caller(a)} if a else None),
        "started_at": start, "ended_at": end_t if status in ENDED else None,
        "stages": [st[k] for k, _ in STAGES],
        "fixture": fx,
        "server_now": now,
    }


def router(call: Callable[..., Awaitable[Any]], reader: Callable[..., Any]) -> APIRouter:
    r = APIRouter()
    fx_cache: dict[str, Any] = {"t": 0.0, "data": None}
    run_cache: dict[tuple, tuple[float, dict]] = {}
    lock = asyncio.Lock()

    async def soft(name: str, *a: Any, **kw: Any) -> dict | None:
        try:
            out = await call(name, *a, **kw)
        except HTTPException as e:
            if e.status_code == 404:
                raise
            return {"available": False, "reason": _scrub(e.detail)}
        except Exception as e:  # noqa: BLE001
            return {"available": False, "reason": type(e).__name__}
        return out if isinstance(out, dict) else None

    async def fixture_now() -> dict:
        from shimpaku.actions import fixture
        from shimpaku.api.routes_viz import fixture_state
        if fx_cache["data"] is None or time.time() - fx_cache["t"] > FIXTURE_TTL_S:
            pc = await soft("port_get_config", fixture.DEVICE, fixture.IFNAME)
            fx_cache["data"] = fixture_state(pc)
            fx_cache["t"] = time.time()
        return fx_cache["data"]

    async def fixture_actions() -> list[dict]:
        acts = await soft("action_list") or {}
        rows = [a for a in acts.get("actions") or [] if isinstance(a, dict) and is_fixture_action(a)]
        return sorted(rows, key=lambda a: -(_created(a) or 0))

    async def build(action_id: str | None, visitor: bool) -> dict:
        acts = await fixture_actions()
        audits = await asyncio.gather(*(soft("audit_query", tool=t, limit=200) for t in BREAK_TOOLS))
        audit = {"rows": [r for a in audits for r in (a or {}).get("rows") or []],
                 **({"demo": True} if any((a or {}).get("demo") for a in audits) else {})}
        breaks = break_rows(audit)
        a = None
        if action_id:
            got = await soft("action_get", action_id)
            if not got or got.get("available") is False or not is_fixture_action(got):
                raise HTTPException(404, "theatre follows the demo fixture action only")
            a = got
        elif visitor:
            a = next((x for x in acts if x.get("status") in COMPLETED), None)
        else:
            a = acts[0] if acts else None
            # a fault newer than the newest proposal starts a new run that has no action yet
            if breaks and (a is None or breaks[0]["t"] > (_created(a) or 0)):
                a = None
        if a is not None and action_id is None:
            a = await soft("action_get", a["id"]) or a  # the full record (list rows may be trimmed)
        created = _created(a)
        older = [_created(x) or 0 for x in acts if created and (_created(x) or 0) < created]
        prev = max(older) if older else None
        if a is None and breaks and acts:
            prev = _created(acts[0])
        brk_t = next((b["t"] for b in breaks if (created is None or b["t"] <= created)), None)
        start = brk_t or created or time.time()
        win = max(5, min(24 * 60, int((time.time() - start) / 60) + 2))
        sle, incs = await asyncio.gather(soft("sle_get_summary", "campus", window_min=win), soft("incident_list"))
        live = a is None or a.get("status") not in ENDED
        fx = await fixture_now() if live else None
        run = build_run(a, breaks, prev, sle=sle, incidents=incs, fixture_state=fx, live=live)
        if acts and run["mode"] == "none" and visitor:
            run["note"] = "No completed run yet"
        if (audit or {}).get("demo") or (a or {}).get("demo") or (sle or {}).get("demo"):
            run["demo"] = True
        return run

    async def cached(action_id: str | None, visitor: bool) -> dict:
        key = (action_id, visitor)
        async with lock:
            hit = run_cache.get(key)
            if hit and time.time() - hit[0] < RUN_TTL_S:
                return hit[1]
            run = await build(action_id, visitor)
            run_cache[key] = (time.time(), run)
            return run

    def _visitor(p: Any) -> bool:
        return getattr(p, "effective_role", None) == "visitor"

    @r.get("/api/theatre")
    async def theatre(action: str | None = None, p=Depends(reader)) -> dict:
        """The current (or ``?action=<id>``) demo-fixture run, stage by stage, with real times.
        Visitors get the most recent completed run (a replay) unless they name one."""
        out = await cached(action, _visitor(p))
        return {**out, "server_now": time.time()}

    @r.get("/api/theatre/runs")
    async def theatre_runs(p=Depends(reader)) -> dict:
        """Recent demo-fixture actions, newest first (id, status, created) for a run picker."""
        acts = await fixture_actions()
        return {"runs": [{"id": a.get("id"), "status": a.get("status"), "created_at": _created(a)}
                         for a in acts[:20]]}

    @r.get("/api/stream/theatre")
    async def stream_theatre(request: Request, action: str | None = None, p=Depends(reader)):
        visitor = _visitor(p)

        async def gen():
            last = None
            while not await request.is_disconnected():
                try:
                    run = await cached(action, visitor)
                except HTTPException as e:
                    yield {"event": "error", "data": json.dumps({"status": e.status_code, "detail": e.detail})}
                    return
                blob = json.dumps({k: v for k, v in run.items() if k != "server_now"}, default=str)
                if blob != last:
                    last = blob
                    yield {"event": "theatre", "data": json.dumps({**run, "server_now": time.time()}, default=str)}
                await asyncio.sleep(STREAM_TICK_S)

        return EventSourceResponse(gen(), ping=15)

    return r
