"""virga — command line.

    virga list      [--have day-06-fabric,day-02-campus]   catalogue with run/farm marks
    virga validate                                          schema check, no lab access
    virga selftest  [--only ID,ID] [--repeat N] [--publish] inject, prove, revert, prove (exit 1 on any fail)
    virga plan      --seed S [--count N] [--gap 600:1200] [--control-p 0.2] [--split dev|test]
                    [--inject-mode confirmed|plain] [--only ...]
    virga run       PLAN.json [--agent none|file:PATH|shimpaku[:URL]] [--no-decoys] [--no-b0b] [--publish]
    virga score     RUN_DIR [--publish]
    virga recover   [--pending-wait S]                      revert everything the journal still lists
    virga status                                            journal entries + live device leases
    virga freeze                                            record the catalogue hash for test splits

Paths: see virga/paths.py (VIRGA_STORE private 0700, VIRGA_PUBLISH public results).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from virga import paths
from virga.catalogue import CatalogueError, load


def _cat(args):
    return load(Path(args.catalogue), paths.holdout())


def _ctx(args, cat, mode="selftest", inject_mode="confirmed"):
    from virga.journal import Journal
    from virga.lab import Lab
    from virga.runner import Context

    store = paths.ensure_private(paths.store())
    lab = Lab(cat.lab, set(args.allow_node or []))
    return Context(lab=lab, cat=cat, journal=Journal(store / "journal.json"), mode=mode, inject_mode=inject_mode)


def cmd_list(args) -> int:
    cat = _cat(args)
    have = set(args.have.split(","))
    run = set(cat.runnable(have))
    for i, s in cat.scenarios.items():
        mark = "run " if i in run else ("farm" if "aiops-farm" in s["requires"] else "    ")
        print(f"{mark} {i:8} {s['status']:11} {s['layer']:16} {s['ground_truth']['fault_class']:22} {s['title']}"
              f"  [{', '.join(s['requires'])}]{' HOLDOUT' if i in cat.holdout_ids else ''}")
    farm = sum(1 for s in cat.scenarios.values() if "aiops-farm" in s["requires"])
    print(f"\n{len(cat.scenarios)} scenarios: {len(run)} runnable with --have {args.have}, "
          f"{sum(s['status'] == 'selftested' for s in cat.scenarios.values())} self-tested, {farm} need the client farm")
    return 0


def cmd_validate(args) -> int:
    try:
        cat = _cat(args)
    except CatalogueError as e:
        print(e)
        return 1
    print(f"ok: {len(cat.scenarios)} scenarios, sha256 {cat.sha256[:16]}, holdout {len(cat.holdout_ids)}")
    return 0


def _summary_table(recs):
    print(f"\n{'scenario':9} {'verdict':8} {'effect_s':>8} {'restore_s':>9} {'cfg_restored':>12}  title")
    for r in recs:
        vi, vr = r["phases"].get("verify_injected", {}), r["phases"].get("verify_reverted", {})
        print(f"{r['scenario']:9} {r['verdict'][:8]:8} {str(vi.get('time_to_effect_s')):>8} "
              f"{str(vr.get('time_to_restore_s')):>9} {str(vr.get('config_restored')):>12}  {r.get('title', '')}")


def cmd_selftest(args) -> int:
    from virga import publish
    from virga.runner import execute, log

    cat = _cat(args)
    have = set(args.have.split(","))
    chosen = [i.strip() for i in args.only.split(",")] if args.only else cat.runnable(have)
    for i in chosen:
        if i not in cat.scenarios or not cat.scenarios[i].get("executable"):
            print(f"{i}: unknown or not executable")
            return 2
    ctx = _ctx(args, cat)
    out = paths.ensure_private(paths.store() / "selftest")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results = out / f"selftest-{stamp}.jsonl"
    cooldown = cat.lab["timing_defaults"].get("cooldown_s", 10)
    recs = []
    try:
        for rep in range(args.repeat):
            for sid in chosen:
                log(f"--- repeat {rep + 1}/{args.repeat}")
                r = execute(ctx, cat.scenarios[sid])
                r["repeat"] = rep + 1
                recs.append(r)
                with results.open("a") as fh:
                    fh.write(json.dumps(r, default=str) + "\n")
                time.sleep(cooldown)
    finally:
        ctx.lab.drop()
    _summary_table(recs)
    print(f"\nresults: {results}")
    # only this invocation's own trial ids: the journal is shared with any concurrently running
    # process (another builder, or a bench run on a different node), and its in-flight entries are
    # not residue of this selftest
    mine = {r["trial_id"] for r in recs}
    left = {k: v for k, v in ctx.journal.entries().items() if k in mine}
    if left:
        print(f"JOURNAL NOT EMPTY (this run's own trials): {list(left)} -- run `virga recover`")
    if args.publish:
        pub = paths.publish_dir()
        data = publish.load(pub)
        data["catalogue_sha256"] = cat.sha256
        data["selftest"] = publish.selftest_summary(recs, cat, [results.name])
        print(f"published {publish.write(pub, data)}")
    return 0 if recs and all(r["verdict"] == "pass" for r in recs) and not left else 1


def cmd_plan(args) -> int:
    from virga.plan import make_plan

    cat = _cat(args)
    have = set(args.have.split(","))
    if args.split == "test":
        fz = paths.store() / "freeze.json"
        if not fz.exists() or json.loads(fz.read_text()).get("catalogue_sha256") != cat.sha256:
            print("test split refused: catalogue differs from the frozen hash (run `virga freeze` on the tagged commit)")
            return 2
    ids = [i.strip() for i in args.only.split(",")] if args.only else cat.benchable(have, args.split == "test")
    lo, hi = (int(x) for x in args.gap.split(":"))
    plan = make_plan(cat, ids, args.seed, args.count, (lo, hi), args.control_p, args.split, args.inject_mode)
    d = paths.ensure_private(paths.store() / "plans")
    p = d / f"plan-{args.split}-s{args.seed}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    p.write_text(json.dumps(plan, indent=1))
    span = plan["items"][-1]["t_offset_s"] + plan["items"][-1]["hold_s"]
    print(f"{p}\n{sum(i['kind'] == 'fault' for i in plan['items'])} fault + "
          f"{sum(i['kind'] == 'control' for i in plan['items'])} null trials (null share {plan['null_share']}), "
          f"{len(ids)} scenarios, span {span / 3600:.1f} h, inject {args.inject_mode}")
    return 0


def cmd_run(args) -> int:
    from virga import adapters
    from virga.decoys import DecoyGen
    from virga.runner import control_window, execute, log

    cat = _cat(args)
    plan = json.loads(Path(args.plan).read_text())
    if plan.get("catalogue_sha256") != cat.sha256:
        print("refused: the catalogue changed since this plan was made (plans are pinned to a catalogue hash)")
        return 2
    ctx = _ctx(args, cat, mode="bench", inject_mode=plan.get("inject_mode", "confirmed"))
    ctx.adapter = adapters.make(args.agent)
    ctx.decoys = None if args.no_decoys else DecoyGen(cat.lab, plan["seed"])
    ctx.b0b = not args.no_b0b
    run_id = f"R-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{plan['split']}-s{plan['seed']}"
    rd = paths.ensure_private(paths.store() / "runs" / run_id)
    (rd / "plan.json").write_text(json.dumps(plan, indent=1))
    recs_path = rd / "records.jsonl"
    t0 = time.time()
    log(f"run {run_id}: {len(plan['items'])} items, adapter {ctx.adapter.name}, decoys {bool(ctx.decoys)}, b0b {ctx.b0b}")
    try:
        for it in plan["items"]:
            time.sleep(max(0.0, t0 + it["t_offset_s"] - time.time()))
            key = f"{run_id}-{it['n']:03d}"
            if it["kind"] == "control":
                r = control_window(ctx, it["hold_s"], key)
            else:
                r = execute(ctx, cat.scenarios[it["scenario"]], it.get("intensity"), it.get("hold_s"), key)
            r["block"], r["plan_n"] = it["block"], it["n"]
            with recs_path.open("a") as fh:
                fh.write(json.dumps(r, default=str) + "\n")
            (rd / "incidents_b0b.jsonl").write_text("".join(json.dumps(i) + "\n" for i in ctx.incidents_b0b))
    finally:
        ctx.lab.drop()
        try:
            incs = ctx.adapter.incidents(since=t0 - 60)
            if ctx.adapter.name != "none":
                (rd / f"incidents_{ctx.adapter.name}.jsonl").write_text("".join(json.dumps(i) + "\n" for i in incs))
        except Exception as e:
            log(f"adapter {ctx.adapter.name}: could not fetch incidents: {type(e).__name__}: {e}")
        (rd / "incidents_null.jsonl").write_text("")
    log(f"run done: {rd}")
    args.run_dir = str(rd)
    return cmd_score(args)


def cmd_score(args) -> int:
    from virga import publish
    from virga.scoring import class_stats, fmt_table, records_to_trials, run_meta, score_system

    rd = Path(args.run_dir)
    recs = [json.loads(ln) for ln in (rd / "records.jsonl").read_text().splitlines() if ln.strip()]
    plan = json.loads((rd / "plan.json").read_text()) if (rd / "plan.json").exists() else {}
    trials = records_to_trials(recs, rd.name, plan.get("seed"))
    if not [t for t in trials if t["fault_class"] != "none"]:
        print("no proven fault trials: nothing to score")
        return 1
    run = run_meta(trials, rd.name, [plan.get("seed")], split=plan.get("split"), inject_mode=plan.get("inject_mode"))
    (rd / "trials.jsonl").write_text("".join(json.dumps(t) + "\n" for t in trials))
    (rd / "run.json").write_text(json.dumps(run, indent=1))
    S, PER, per_class, cci = [], [], {}, {}
    for f in sorted(rd.glob("incidents_*.jsonl")):
        name = f.stem[len("incidents_"):]
        incs = [json.loads(ln) for ln in f.read_text().splitlines() if ln.strip()]
        s, P = score_system(trials, incs, run, name)
        cs = class_stats(P)
        S.append(s)
        PER.append(P)
        per_class[name], cci[name] = cs["per_class"], cs["cluster_ci"]
    md = fmt_table(S)
    (rd / "scores.md").write_text(f"Run {rd.name} ({plan.get('split')}, seed {plan.get('seed')}, "
                                  f"inject {plan.get('inject_mode')})\n\n{md}\n")
    (rd / "scores.json").write_text(json.dumps(publish._clean({"systems": S, "per_class": per_class, "cluster_ci": cci}), indent=1))
    publish.scoreboard_svg(S, PER, rd / "scoreboard.svg")
    print(md)
    if getattr(args, "publish", False):
        pub = paths.publish_dir()
        data = publish.load(pub)
        data["latest_run"] = {"run_id": rd.name, "split": plan.get("split"), "seed": plan.get("seed"),
                              "inject_mode": plan.get("inject_mode"),
                              "fault_trials": sum(t["fault_class"] != "none" for t in trials),
                              "null_trials": sum(t["fault_class"] == "none" for t in trials),
                              "systems": S, "per_class": per_class, "cluster_ci": cci, "table_md": md,
                              "plot": "scoreboard.svg"}
        data["runs"] = [r for r in data.get("runs", []) if r["run_id"] != rd.name] + [
            {"run_id": rd.name, "split": plan.get("split"), "generated": datetime.now(timezone.utc).isoformat()}]
        publish.write(pub, data)
        (pub / "scoreboard.svg").write_text((rd / "scoreboard.svg").read_text())
        (pub / "scores.md").write_text((rd / "scores.md").read_text())
        print(f"published {pub / 'results.json'}")
    return 0


def cmd_recover(args) -> int:
    from virga.recover import recover

    cat = _cat(args)
    ctx = _ctx(args, cat)
    try:
        return recover(ctx, ctx.journal, args.pending_wait)
    finally:
        ctx.lab.drop()


def cmd_status(args) -> int:
    from shimpaku import lock

    from virga.journal import Journal

    j = Journal(paths.store() / "journal.json").entries()
    print(f"store {paths.store()}  journal entries: {len(j)}")
    for k, e in j.items():
        print(f"  {k}: {e.get('scenario')} nodes={e.get('nodes')} mode={e.get('inject_mode')} decoys={len(e.get('decoys', []))}")
    try:
        hs = lock.holders(db=paths.lock_db())
        paths.share_lock_db()
    except Exception as e:
        print(f"lock db {paths.lock_db()}: {type(e).__name__}: {e}")
        return 1
    print(f"lock db {paths.lock_db()}  live leases: {len(hs)}")
    for h in hs:
        print(f"  {h['device']}: {h['holder']} ({h['purpose']}) until {time.ctime(h['expires'])}")
    return 1 if j else 0


def cmd_freeze(args) -> int:
    cat = _cat(args)
    p = paths.ensure_private(paths.store()) / "freeze.json"
    p.write_text(json.dumps({"catalogue_sha256": cat.sha256, "frozen": datetime.now(timezone.utc).isoformat(),
                             "holdout": sorted(cat.holdout_ids)}, indent=1))
    print(f"frozen {cat.sha256} -> {p}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="virga", description="Shimpaku fault-injection benchmark")
    ap.add_argument("-c", "--catalogue", default=str(paths.catalogue()))
    ap.add_argument("--have", default="day-06-fabric,day-02-campus")
    ap.add_argument("--allow-node", action="append", default=[])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("validate")
    p = sub.add_parser("selftest")
    p.add_argument("--only")
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--publish", action="store_true")
    p = sub.add_parser("plan")
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--count", type=int, default=85)
    p.add_argument("--gap", default="600:1200")
    p.add_argument("--control-p", type=float, default=0.2)
    p.add_argument("--split", choices=["dev", "test"], default="dev")
    p.add_argument("--inject-mode", choices=["confirmed", "plain"], default="confirmed")
    p.add_argument("--only")
    p = sub.add_parser("run")
    p.add_argument("plan")
    p.add_argument("--agent", default="none")
    p.add_argument("--no-decoys", action="store_true")
    p.add_argument("--no-b0b", action="store_true")
    p.add_argument("--publish", action="store_true")
    p = sub.add_parser("score")
    p.add_argument("run_dir")
    p.add_argument("--publish", action="store_true")
    p = sub.add_parser("recover")
    p.add_argument("--pending-wait", type=float, default=None)
    sub.add_parser("status")
    sub.add_parser("freeze")
    a = ap.parse_args(argv)
    os.umask(0o007)  # the shared lock DB (and its -wal/-shm) must stay writable for group shimpaku
    fn = {"list": cmd_list, "validate": cmd_validate, "selftest": cmd_selftest, "plan": cmd_plan, "run": cmd_run,
          "score": cmd_score, "recover": cmd_recover, "status": cmd_status, "freeze": cmd_freeze}[a.cmd]
    try:
        return fn(a)
    except CatalogueError as e:
        print(e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
