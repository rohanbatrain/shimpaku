"""One trial: preflight -> inject -> verify_injected -> [agent window] -> revert -> verify_reverted.

Self-test (no agent window) proves that each catalogued fault actually happens and actually goes away.
Bench mode adds the agent window, decoys, the B0b capture and remediation verification.

Inject modes (REVIEW A1):
  confirmed  each inject commit is `commit confirmed <ttl>` (dead man's switch). Self-test and run A.
  plain      plain `commit`; the runner's journal (inverse steps + snapshot) is the only way back,
             via the trial's own revert or `virga recover`. Run B, so an agent's fix is not refused
             for a pending confirm and the fault does not heal itself at the TTL.
Lockout scenarios (`auto` revert) always inject confirmed: the device's own rollback is the revert.
"""

from __future__ import annotations

import contextlib
import difflib
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from virga import RUN_SCHEMA
from virga.adapters import Adapter
from virga.baselines import b0b_incident
from virga.catalogue import Catalogue, expand_probes, intensity_of, scenario_env, subst, timing, touched_nodes
from virga.decoys import DecoyGen
from virga.decoys import apply as decoy_apply
from virga.decoys import revert_all as decoy_revert_all
from virga.journal import Journal
from virga.lab import Lab, host_load
from virga.probes import poll, sample

REVERT_CONFIRM_MIN = 2


def iso(t: float | None = None) -> str:
    return datetime.fromtimestamp(t if t is not None else time.time(), timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")


def log(msg: str) -> None:
    print(f"{iso()} {msg}", flush=True)


@dataclass
class Context:
    lab: Lab
    cat: Catalogue
    journal: Journal
    mode: str = "selftest"            # selftest | bench
    inject_mode: str = "confirmed"    # confirmed | plain
    adapter: Adapter = field(default_factory=Adapter)
    decoys: DecoyGen | None = None
    b0b: bool = False
    b0b_delay_s: float = 60.0
    incidents_b0b: list = field(default_factory=list)


def config_diff(before: list[str], after: list[str]) -> list[str]:
    """Ordered line diff (filter-term order matters): only the +/- lines."""
    return [d for d in difflib.unified_diff(before, after, lineterm="", n=0)
            if d[:1] in "+-" and not d.startswith(("+++", "---"))]


def run_steps(ctx: Context, steps: list[dict], phase: str, ttl_min: int, auto_wait: list) -> list[dict]:
    lab = ctx.lab
    out = []
    for st in steps:
        if "junos" in st:
            j = st["junos"]
            if phase == "inject":
                plain = ctx.inject_mode == "plain" and "ttl_min" not in j
                out.append(lab.commit(j["node"], j["lines"], None if plain else int(j.get("ttl_min", ttl_min))))
            else:
                out.append(lab.commit(j["node"], j["lines"], REVERT_CONFIRM_MIN, confirm_after=True))
        elif "flap" in st:
            f = st["flap"]
            node, ifn = f["node"], f["ifname"]
            trans = []
            for _ in range(int(f.get("cycles", 3))):
                r = lab.commit(node, [f"set interfaces {ifn} disable"], REVERT_CONFIRM_MIN, confirm_after=True)
                trans.append({"down": iso(r.get("t_commit"))})
                time.sleep(float(f.get("down_s", 8)))
                r = lab.commit(node, [f"delete interfaces {ifn} disable"], REVERT_CONFIRM_MIN, confirm_after=True)
                trans[-1]["up"] = iso(r.get("t_commit"))
                time.sleep(float(f.get("up_s", 20)))
            out.append({"node": node, "flap": ifn, "transitions": trans, "changed": True, "t_commit": time.time()})
        elif "junos_op" in st:
            out.append(lab.op(st["junos_op"]["node"], st["junos_op"]["cmd"]))
        elif "sh" in st:
            import subprocess

            s = st["sh"]
            argv = ["bash", "-c", s["cmd"]] if not s.get("on") else ["ssh", "-o", "BatchMode=yes", s["on"], s["cmd"]]
            r = subprocess.run(argv, capture_output=True, text=True, timeout=s.get("timeout", 60))
            out.append({"sh": s["cmd"], "rc": r.returncode, "out": (r.stdout + r.stderr)[-300:]})
            if r.returncode != 0 and not s.get("allow_fail"):
                raise RuntimeError(f"shell step failed rc={r.returncode}: {s['cmd']}")
        elif "wait" in st:
            time.sleep(st["wait"])
            out.append({"wait": st["wait"]})
        elif "close_sessions" in st:
            for n in st["close_sessions"]:
                lab.drop(n)
            out.append({"closed": st["close_sessions"]})
        elif "auto" in st:
            out.append({"auto": st["auto"], "note": "device reverts itself when the inject's commit-confirmed timer lapses"})
            auto_wait.append(st["auto"])
        else:
            raise ValueError(f"unknown step {st}")
    return out


def wait_no_pending(lab: Lab, nodes: list[str], timeout_s: float) -> dict:
    t0 = time.time()
    state = {}
    while True:
        state = {}
        for n in nodes:
            try:
                state[n] = lab.pending(n)
            except Exception as e:
                state[n] = f"error:{type(e).__name__}"
                lab.drop(n)
        if not any(v is True or isinstance(v, str) for v in state.values()) or time.time() - t0 > timeout_s:
            return state
        time.sleep(5)


def _accepts(sc: dict, action: dict) -> bool:
    for x in (sc.get("remediation") or {}).get("accept", [None]):
        if x and action.get("tool") == x.get("tool") and all(
                (action.get("args") or {}).get(k) == v for k, v in (x.get("args") or {}).items()):
            return True
    return False


def agent_window(ctx: Context, sc: dict, key: str, t_ref: float, hold_s: float, probes: list[dict],
                 nodes: list[str], fault_probes: list[dict]) -> dict:
    """The system under test detects, diagnoses and may act. Virga only watches, makes decoys,
    captures B0b, and verifies any fix independently (t_fix_verified)."""
    lab = ctx.lab
    plain = ctx.inject_mode == "plain"
    t_end = t_ref + hold_s
    decoys = ctx.decoys.schedule(t_ref, hold_s, nodes, plain) if ctx.decoys else []
    applied, decoy_log, actions, t_fix, b0b_done = [], [], [], None, not ctx.b0b
    ttl = timing(ctx.cat.lab, sc)["inject_ttl_min"]
    while time.time() < t_end:
        now = time.time()
        while decoys and decoys[0]["t_at"] <= now:
            d = decoys.pop(0)
            res = decoy_apply(lab, ctx.journal, key, d, None if plain else ttl)
            decoy_log.append(res)
            if res.get("applied"):
                applied.append({**d, **res})
        if not b0b_done and now >= t_ref + ctx.b0b_delay_s:
            ok, _ = sample(lab, [p for p in probes if p.get("guard")], "healthy")
            inc = b0b_incident(lab, list(ctx.cat.lab["nodes"]), not ok, time.time(), key)
            if inc:
                ctx.incidents_b0b.append(inc)
            b0b_done = True
        try:
            acts = ctx.adapter.actions(since=t_ref)
        except Exception as e:  # the daemon may be down; that is the system's failure, not the runner's
            acts = []
            decoy_log.append({"adapter_error": f"{type(e).__name__}: {str(e)[:120]}"})
        actions = acts
        if acts and t_fix is None:
            f = poll(lab, fault_probes, "healthy", 30, 5, 2)
            if f["ok"]:
                t_fix = f["t_first_ok"]
                break
        time.sleep(10)
    if not b0b_done:
        ok, _ = sample(lab, [p for p in probes if p.get("guard")], "healthy")
        inc = b0b_incident(lab, list(ctx.cat.lab["nodes"]), not ok, time.time(), key)
        if inc:
            ctx.incidents_b0b.append(inc)
    end_ok, rows = sample(lab, fault_probes, "healthy")
    unsafe = [a for a in actions if not _accepts(sc, a)]
    return {"adapter": ctx.adapter.name, "actions": actions, "unsafe_actions": len(unsafe),
            "t_fix_verified": t_fix, "fault_cleared_at_window_end": end_ok, "probe_values_at_window_end": rows,
            "decoys": decoy_log, "applied_decoys": applied, "window_s": round(time.time() - t_ref, 1)}


def execute(ctx: Context, sc: dict, intensity: dict | None = None, hold_s: float | None = None,
            trial_id: str | None = None) -> dict:
    lab, cat = ctx.lab, ctx.cat
    key = trial_id or f"{sc['id']}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:4]}"
    env = scenario_env(cat.lab, sc, intensity)
    probes = expand_probes(cat.lab, sc, env)
    inject, revert = subst(sc["inject"], env), subst(sc["revert"], env)
    tm = timing(cat.lab, sc)
    nodes = touched_nodes(inject) + [n for n in touched_nodes(revert) if n not in touched_nodes(inject)]
    write_nodes = [n for n in nodes if any(n == s.get(k, {}).get("node") for s in inject + revert
                                           for k in ("junos", "junos_op", "flap"))]
    has_auto = any("auto" in s for s in revert)
    inject_mode = "confirmed" if has_auto else ctx.inject_mode
    rec = {"schema": RUN_SCHEMA, "trial_id": key, "mode": ctx.mode, "inject_mode": inject_mode,
           "scenario": sc["id"], "title": sc["title"], "layer": sc["layer"], "intensity": intensity_of(sc, env),
           "nodes_touched": nodes, "t_start": iso(), "host_load_1m": host_load(), "phases": {},
           "catalogue_sha256": cat.sha256, "holdout": sc["id"] in cat.holdout_ids,
           "ground_truth": sc["ground_truth"], "expect_inject_rejected": bool(sc.get("expect_inject_rejected"))}
    log(f"=== {sc['id']} {sc['title']}  trial {key}  ({inject_mode})")

    # 1 preflight: healthy baseline, nothing pending on a touched node
    pend = {n: lab.pending(n) for n in nodes}
    pre = poll(lab, probes, "healthy", tm["preflight_timeout_s"], tm["poll_s"], 1)
    pre["pending_commits"] = pend
    rec["phases"]["preflight"] = pre
    if not pre["ok"] or any(pend.values()):
        rec["verdict"] = "blocked: baseline not healthy" if not pre["ok"] else "blocked: commit pending"
        rec["t_end"] = iso()
        log(f"    {rec['verdict']}: " + "; ".join(f"{r['probe']}={r['value']}" for r in pre["values"] if r["ok"] is False))
        return rec

    hold = contextlib.ExitStack()
    saved_mode = ctx.inject_mode
    ctx.inject_mode = inject_mode
    try:
        hold.enter_context(lab.leased(write_nodes, check_pending=True))
        snapshot = {n: lab.config_lines(n) for n in nodes}
        log(f"    preflight ok ({pre['elapsed_s']} s); lease {write_nodes}; snapshot "
            + ", ".join(f"{n}={len(v)} lines" for n, v in snapshot.items()))

        # 2 inject -- journaled BEFORE the first commit
        ctx.journal.put(key, {"scenario": sc["id"], "t_inject": iso(), "inject_mode": inject_mode, "nodes": nodes,
                              "write_nodes": write_nodes, "revert": revert, "snapshot": snapshot,
                              "ttl_min": tm["inject_ttl_min"], "decoys": []})
        auto_wait: list = []
        t_ref = time.time()
        try:
            steps = run_steps(ctx, inject, "inject", tm["inject_ttl_min"], auto_wait)
            commits = [s for s in steps if s.get("changed")]
            t_ref = max([s["t_commit"] for s in commits], default=time.time())
            rec["phases"]["inject"] = {"ok": True, "steps": steps, "t_ref": iso(t_ref)}
            ctx.journal.update(key, t_ref=iso(t_ref))
            if sc.get("expect_inject_rejected"):
                raise RuntimeError("inject was expected to be rejected but committed")
            log(f"    injected: {len(commits)} commit(s), "
                + ", ".join(f"{s['node']} {s.get('commit_s', '-')} s" for s in commits))
        except Exception as e:
            rejected = bool(sc.get("expect_inject_rejected")) and "expected to be rejected" not in str(e)
            rec["phases"]["inject"] = {"ok": rejected, "rejected": rejected,
                                       "error": f"{type(e).__name__}: {str(e)[:400]}"}
            log(f"    inject {'rejected as expected' if rejected else 'FAILED'}: {type(e).__name__}: {str(e)[:160]}")

        # 3 verify injected
        fault_probes = [p for p in probes if "faulted" in p]
        if rec["phases"]["inject"]["ok"] and not rec["phases"]["inject"].get("rejected"):
            fast = [p for p in fault_probes if p.get("ttd")]
            vi: dict = {}
            if fast:
                f = poll(lab, fast, "faulted", tm["inject_timeout_s"], tm["fast_poll_s"], 1)
                vi["time_to_effect_s"] = round(f["t_first_ok"] - t_ref, 2) if f["ok"] else None
                vi["fast_probe"] = f
            full = poll(lab, fault_probes, "faulted", tm["inject_timeout_s"], tm["poll_s"], tm["stable"])
            vi.update(ok=full["ok"] and (not fast or vi["fast_probe"]["ok"]), confirm=full)
            if not fast and full["ok"]:
                vi["time_to_effect_s"] = round(full["t_first_ok"] - t_ref, 2)
            vi["observed"] = sample(lab, [p for p in probes if "faulted" not in p and not p.get("guard")], None)[1]
            rec["phases"]["verify_injected"] = vi
            log(f"    verify_injected {'OK' if vi['ok'] else 'FAILED'}  time_to_effect={vi.get('time_to_effect_s')} s; "
                + "; ".join(f"{r['probe']}={r['value']}" for r in full["values"]))
            if ctx.mode == "bench" and vi["ok"]:
                if inject_mode == "plain":
                    hold.close()  # run B: the system under test may write; the pending check protects run A
                rec["phases"]["agent"] = agent_window(ctx, sc, key, t_ref, hold_s or tm["detect_budget_s"], probes,
                                                      nodes, [p for p in fault_probes if "healthy" in p] or
                                                      [p for p in probes if p.get("guard")])
        elif rec["phases"]["inject"].get("rejected"):
            # nothing must have changed: an unchanged config is the "faulted" evidence
            after = {n: lab.config_lines(n) for n in nodes}
            same = all(not config_diff(snapshot[n], after[n]) for n in nodes)
            rec["phases"]["verify_injected"] = {"ok": same, "config_unchanged": same, "time_to_effect_s": None}
            log(f"    verify_injected {'OK' if same else 'FAILED'}  config unchanged after rejection: {same}")

        # 4 revert (always) and 5 verify reverted
        try:
            if inject_mode == "plain" and ctx.mode == "bench":
                rec["phases"]["pre_revert_pending"] = wait_no_pending(lab, nodes, 600)
            hold.enter_context(lab.leased(write_nodes, check_pending=False))
            applied = (rec["phases"].get("agent") or {}).get("applied_decoys", [])
            dsteps = decoy_revert_all(lab, ctx.journal, key, applied) if applied else []
            rsteps = run_steps(ctx, revert, "revert", tm["inject_ttl_min"], auto_wait) \
                if rec["phases"]["inject"].get("steps") or not rec["phases"]["inject"]["ok"] else []
            t_rev = max([s.get("t_commit", 0) for s in rsteps], default=0) or time.time()
            rec["phases"]["revert"] = {"ok": True, "steps": rsteps, "decoys_reverted": dsteps}
            budget = tm["revert_timeout_s"] + (60 * tm["inject_ttl_min"] + 120 if auto_wait else 0)
            if auto_wait:
                budget = tm["revert_timeout_s"] + 60 * max(
                    int(s["junos"].get("ttl_min", tm["inject_ttl_min"])) for s in inject if "junos" in s) + 120
            vr = poll(lab, probes, "healthy", budget, tm["poll_s"], tm["stable"])
            if vr["ok"]:
                vr["time_to_restore_s"] = round(vr["t_first_ok"] - (t_ref if auto_wait else t_rev), 2)
            after = {n: lab.config_lines(n) for n in nodes}
            diffs = {n: config_diff(snapshot[n], after[n]) for n in nodes}
            vr["config_restored"] = all(not d for d in diffs.values())
            vr["config_diff"] = {n: d for n, d in diffs.items() if d}
            vr["config_lines_compared"] = {n: len(snapshot[n]) for n in nodes}
            vr["pending_commits"] = {n: lab.pending(n) for n in nodes}
            rec["phases"]["verify_reverted"] = vr
            log(f"    verify_reverted {'OK' if vr['ok'] else 'FAILED'}  time_to_restore={vr.get('time_to_restore_s')} s; "
                f"config_restored={vr['config_restored']} ({sum(vr['config_lines_compared'].values())} lines) "
                f"pending={vr['pending_commits']}")
            if vr["config_diff"]:
                log(f"    RESIDUE: {vr['config_diff']}")
            if vr["ok"] and vr["config_restored"] and not any(vr["pending_commits"].values()):
                ctx.journal.remove(key)
        except Exception as e:
            rec["phases"]["revert"] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:400]}"}
            log(f"    REVERT FAILED: {e} -- the journal keeps {key}; run `virga recover`")
    finally:
        hold.close()
        ctx.inject_mode = saved_mode

    ph = rec["phases"]
    vr = ph.get("verify_reverted", {})
    rec["verdict"] = "pass" if (ph["inject"]["ok"] and ph.get("verify_injected", {}).get("ok")
                                and ph.get("revert", {}).get("ok") and vr.get("ok") and vr.get("config_restored")
                                and not any(vr.get("pending_commits", {}).values())) else "fail"
    rec["t_end"] = iso()
    log(f"    verdict {rec['verdict'].upper()}")
    return rec


def control_window(ctx: Context, hold_s: float, key: str) -> dict:
    """Null trial: inject nothing, hold, record (false-positive measurement). Decoys still happen."""
    t0 = time.time()
    log(f"=== CTRL-00 control window {hold_s:.0f} s (no fault)  trial {key}")
    rec = {"schema": RUN_SCHEMA, "trial_id": key, "mode": "bench", "scenario": "CTRL-00", "kind": "control",
           "t_start": iso(t0), "host_load_1m": host_load(), "catalogue_sha256": ctx.cat.sha256, "phases": {}}
    applied = []
    if ctx.decoys:
        ctx.journal.put(key, {"scenario": "CTRL-00", "t_inject": iso(t0), "inject_mode": ctx.inject_mode, "nodes": [],
                              "write_nodes": [], "revert": [], "snapshot": {}, "decoys": []})
        for d in ctx.decoys.schedule(t0, hold_s, [], True):
            time.sleep(max(0.0, d["t_at"] - time.time()))
            res = decoy_apply(ctx.lab, ctx.journal, key, d, None if ctx.inject_mode == "plain" else 5)
            if res.get("applied"):
                applied.append({**d, **res})
    if ctx.b0b:
        time.sleep(max(0.0, t0 + ctx.b0b_delay_s - time.time()))
        cat = ctx.cat
        guards = [{**p, "id": f"{n}:{p['id']}", "guard": True} for n, ps in cat.lab["health_sets"].items() for p in ps]
        ok, _ = sample(ctx.lab, guards, "healthy")
        inc = b0b_incident(ctx.lab, list(cat.lab["nodes"]), not ok, time.time(), key)
        if inc:
            ctx.incidents_b0b.append(inc)
    time.sleep(max(0.0, t0 + hold_s - time.time()))
    rec["phases"]["decoys_reverted"] = decoy_revert_all(ctx.lab, ctx.journal, key, applied) if applied else []
    if ctx.decoys:
        ctx.journal.remove(key)
    rec["t_end"] = iso()
    rec["verdict"] = "pass"
    return rec
