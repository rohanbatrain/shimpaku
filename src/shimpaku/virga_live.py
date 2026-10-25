"""Virga live: the operator's fault lab, run inside the daemon (docs/build/virga-live.md D1, D6, D9).

One trial at a time. A trial is `virga.runner.execute` in `demo` mode, in a worker thread: the same
runner, catalogue, device lease (holder ``virga:live:<role>``) and crash journal as the CLI
(``$VIRGA_STORE/journal.json``), so ``virga recover`` on the lab host cleans up after a daemon that
died mid-trial, and a CLI trial and a dashboard trial can never both write.

What leaves this module is built from an allow-list (D3): scenario id, title, layer, site, nodes,
links, phase names, statuses, times, counts and short details the runner writes for this purpose.
Never a config line, a management address, a probe evidence string or a user name: log lines go
through ``public_line`` (config statements and addresses removed), the actor is reduced to a role.
The full runner record (config snapshot, diffs, probe values) is private: it is appended to
``$VIRGA_STORE/live/live-YYYYMMDD.jsonl`` (0700 directory). Public summaries of finished trials
are kept in ``$VIRGA_PUBLISH/virga-live.json`` so the history survives a restart.

D9: while the journal holds an entry (an unfinished trial, ours or a crashed CLI's), injects are
refused with 409 ``needs_recovery`` until the operator presses *Recover* (``virga.recover``).
D7: nothing runs unless ``SHIMPAKU_VIRGA_LIVE=1``; the replay and the canned demo use other classes
(``ReplayVirga`` here, ``shimpaku.api.demo_virga``) with the same methods.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("shimpaku.virga_live")

PHASES = ["preflight", "inject", "verify_injected", "hold", "revert", "verify_reverted"]
DEMO_EXCLUDE = {"CAM-06"}       # management lockout blinds the daemon's own NETCONF and telemetry (D6)
LATENT = {"FAB-03", "CFG-02"}   # no user impact (a catalogue `impact:` field overrides)
HAVE = {"day-06-fabric", "day-02-campus"}
SITE_BY_REQUIRE = {"day-06-fabric": "fabric", "day-02-campus": "campus"}
HOLD_DEFAULT_S, HOLD_MIN_S, HOLD_MAX_S = 180, 30, 600
# Recover waits this long for a pending commit confirmed to lapse (writing earlier would confirm the
# fault), then leaves the entry for the next press: the edge gives a write 100 s to answer.
RECOVER_PENDING_WAIT_S = 60
LOG_KEEP, RECENT_KEEP, HISTORY_KEEP = 50, 10, 200
TRIAL_LINGER_S = 600            # a finished trial stays on the page this long, then `trial` is null
OFF_REASON = "fault injection is off on this lab (SHIMPAKU_VIRGA_LIVE is not set)"
REPLAY_REASON = "the lab is offline; fault injection needs the live lab"
IFNAME = re.compile(r"\b((?:ge|xe|et|mge)-\d+/\d+/\d+)(?:\.\d+)?\b")
CONFIG_STMT = re.compile(r"\b(?:set|delete|insert|deactivate|activate|replace)\s+[a-z][\w-]*(?:\s+[^\s;,'\"\]}]+)*")
HOME = re.compile(r"/(?:home|Users)/[^/\s\"']+")


class Refused(Exception):
    """A refused write: HTTP status, a short code for the audit row, and the message."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def scrub(text: Any, n: int = 240) -> str:
    from shimpaku.service import scrub_hosts
    return HOME.sub("~", scrub_hosts(str(text)))[:n]


def public_line(msg: str) -> str:
    """A runner log line as a viewer may see it: no config statements, no addresses, no home paths."""
    return scrub(CONFIG_STMT.sub("<config>", msg.strip()))


def audit(method: str, caller: dict, args: dict, status: str) -> None:
    """One hash-chained row (D8), through the daemon's audit log; a no-op outside the daemon."""
    from shimpaku import service
    if service._audit is None:
        return
    try:
        service._audit.append(caller=caller, transport="rest", method=method, tool=method, args=args,
                              result_status=status[:40], source="rest")
    except Exception:  # noqa: BLE001  (auditing must not break a trial)
        log.exception("virga audit append failed for %s", method)


# ---------------------------------------------------------------------- catalogue view (shared)

def site_of(sc: dict) -> str:
    return next((SITE_BY_REQUIRE[r] for r in sc.get("requires") or [] if r in SITE_BY_REQUIRE), "campus")


def nodes_of(sc: dict) -> list[str]:
    from virga.catalogue import touched_nodes
    inj = touched_nodes(sc.get("inject") or [])
    return inj + [n for n in touched_nodes(sc.get("revert") or []) if n not in inj]


def links_of(sc: dict, ports: dict[tuple[str, str], str]) -> list[list[str]]:
    """Best effort: interfaces named in the scenario's steps and own probes, mapped to the topology's
    wired ports ``{(node, ifname): peer}``. Unknown interfaces are left out."""
    seen: list[tuple[str, str]] = []
    for st in (sc.get("inject") or []) + (sc.get("revert") or []):
        if "junos" in st:
            seen += [(st["junos"]["node"], m) for ln in st["junos"].get("lines") or [] for m in IFNAME.findall(ln)]
        elif "flap" in st:
            seen.append((st["flap"]["node"], IFNAME.sub(r"\1", st["flap"]["ifname"])))
    for p in sc.get("probes") or []:
        a = p.get("args") or {}
        if a.get("node") and isinstance(a.get("ifname"), str) and IFNAME.search(a["ifname"]):
            seen.append((a["node"], IFNAME.search(a["ifname"]).group(1)))
    out: list[list[str]] = []
    for node, ifn in seen:
        peer = ports.get((node, ifn))
        pair = sorted([node, peer]) if peer else None
        if pair and pair not in out:
            out.append(pair)
    return out


def impact_of(sc: dict) -> str:
    if sc.get("impact") in ("user", "latent"):
        return sc["impact"]
    return "latent" if sc["id"] in LATENT else "user"


def demo_check(sc: dict, holdout: set[str]) -> tuple[bool, str | None]:
    """D6: executable, self-tested, not holdout, not excluded, and runnable on this lab."""
    if not sc.get("executable"):
        missing = sorted(set(sc.get("requires") or []) - HAVE)
        return False, f"needs {', '.join(missing)}" if missing else "not executable on this lab"
    if not set(sc.get("requires") or []) <= HAVE:
        return False, f"needs {', '.join(sorted(set(sc['requires']) - HAVE))}"
    if sc["id"] in DEMO_EXCLUDE:
        return False, "management lockout would blind the daemon's own NETCONF and telemetry"
    if sc["id"] in holdout:
        return False, "held out of the benchmark"
    if sc.get("status") != "selftested":
        return False, "not self-tested yet: a live fault needs a proven inject and revert"
    return True, None


def est_s(sc: dict, lab: dict, st: dict | None) -> int:
    """Typical trial length WITHOUT the hold: preflight, inject, time to effect, revert, time to
    restore; self-test medians when published, else the catalogue's polling."""
    from virga.catalogue import timing
    tm = timing(lab, sc)
    poll = float(tm.get("poll_s", 4)) * float(tm.get("stable", 2))
    tte = (st or {}).get("time_to_effect_s_median")
    ttr = (st or {}).get("time_to_restore_s_median")
    return int(round(10 + 2 * poll + (tte if isinstance(tte, (int, float)) else poll)
                     + (ttr if isinstance(ttr, (int, float)) else poll)))


def _epoch(v: Any) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and v:
        with contextlib.suppress(ValueError):
            return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
    return None


def selftest_counts(results: dict | None) -> dict[str, dict]:
    """Published self-test results (``virga publish``, ``results.json``) per scenario."""
    out = {}
    for sid, s in (((results or {}).get("selftest") or {}).get("scenarios") or {}).items():
        out[sid] = {"passes": int(s.get("passes") or 0), "runs": int(s.get("trials") or 0),
                    "last_at": _epoch(s.get("last")), "_raw": s}
    return out


def catalogue_rows(cat: Any, selftest: dict[str, dict], ports: dict[tuple[str, str], str]) -> list[dict]:
    rows = []
    for sid, sc in cat.scenarios.items():
        if sid in cat.holdout_ids or sc.get("layer") == "control":
            continue  # held-out scenarios are never named publicly; CTRL-00 is not a fault
        ok, why = demo_check(sc, cat.holdout_ids)
        st = selftest.get(sid) or {}
        rows.append({"id": sid, "title": sc["title"], "layer": sc["layer"], "site": site_of(sc),
                     "nodes": nodes_of(sc), "links": links_of(sc, ports), "impact": impact_of(sc),
                     "demo_ok": ok, "why_not": why, "est_s": est_s(sc, cat.lab, st.get("_raw")),
                     "selftest": {"passes": st.get("passes", 0), "runs": st.get("runs", 0),
                                  "last_at": st.get("last_at")}})
    return rows


def selftest_block(selftest: dict[str, dict], generated: Any = None) -> dict:
    return {"generated_at": _epoch(generated), "passes": sum(s["passes"] for s in selftest.values()),
            "runs": sum(s["runs"] for s in selftest.values()),
            "scenarios": {k: {x: v[x] for x in ("passes", "runs", "last_at")} for k, v in sorted(selftest.items())}}


def summary_row(trial: dict, rec: dict | None, incident_opened: bool) -> dict:
    """A finished trial for ``recent`` / ``/api/virga/trials``."""
    ph = (rec or {}).get("phases") or {}
    return {"id": trial["id"], "scenario": trial["scenario"], "title": trial["title"],
            "t_start": trial["t_start"], "t_end": trial.get("_t_end") or time.time(),
            "verdict": trial.get("verdict") or "fail",
            "time_to_effect_s": (ph.get("verify_injected") or {}).get("time_to_effect_s"),
            "time_to_restore_s": (ph.get("verify_reverted") or {}).get("time_to_restore_s"),
            "incident_opened": bool(incident_opened)}


def new_trial(tid: str, sc: dict, role: str, links: list[list[str]], now: float) -> dict:
    return {"id": tid, "scenario": sc["id"], "title": sc["title"], "layer": sc["layer"], "site": site_of(sc),
            "nodes": nodes_of(sc), "links": links, "by_role": role, "t_start": now, "hold_until": None,
            "phase": "preflight", "verdict": None,
            "phases": [{"name": n, "status": "pending", "t_start": None, "t_end": None, "detail": ""} for n in PHASES],
            "log": []}


def public_trial(trial: dict | None) -> dict | None:
    return None if trial is None else {k: copy.deepcopy(v) for k, v in trial.items() if not k.startswith("_")}


def caller_of(p: Any) -> dict:
    """The audit actor for a principal: kind, name, role (operator / presenter / tester / ...)."""
    role = p.effective_role
    if getattr(p, "can_present", False):
        role = "presenter" if p.presenting else "operator"
    kind = {"session": "dashboard", "token": "mcp"}.get(p.kind, p.kind)
    return {"kind": kind, "name": p.name, "role": role, "transport": "rest", "lease_id": p.lease_id}


# ---------------------------------------------------------------------- the live manager

class VirgaLive:
    """The daemon's fault lab. Methods mirror the §2 routes; writes raise ``Refused``."""

    simulated = False

    def __init__(self, *, enabled: bool, reason: str | None = None, store: Path | None = None,
                 publish: Path | None = None, catalogue: Any = None,
                 lab_factory: Callable[[Any, str], Any] | None = None,
                 ports: Callable[[], dict] | None = None, holders: Callable[[], list[dict]] | None = None,
                 execute: Callable[..., dict] | None = None, recover: Callable[..., int] | None = None):
        from virga import paths
        self.enabled = enabled
        self.reason = None if enabled else (reason or OFF_REASON)
        self.store = store or paths.store()
        self.publish = publish or paths.publish_dir()
        self._cat = catalogue
        self.lab_factory = lab_factory or _real_lab
        self.ports_fn = ports or _real_ports
        self.holders_fn = holders or _real_holders
        self.execute_fn = execute
        self.recover_fn = recover
        self.incident_check: Callable[[float, float], bool] | None = None  # set by the router
        self._mu = threading.Lock()
        self._trial: dict | None = None
        self._busy = False
        self._recovering = False
        self._revert = threading.Event()
        self._thread: threading.Thread | None = None
        self._journal_cache: tuple[int | None, dict] = (-1, {})
        self._ports: dict | None = None
        self._results: tuple[float, dict | None] = (-1.0, None)
        self.history: list[dict] = self._load_history()
        if self.enabled and self._journal_entries(fresh=True):
            log.warning("virga live: the journal holds an unfinished trial; injects are refused until Recover")

    # ---- inputs ----------------------------------------------------------------------
    @classmethod
    def from_settings(cls) -> "VirgaLive":
        from shimpaku.config import settings
        return cls(enabled=bool(settings.virga_live))

    def catalogue(self) -> Any:
        if self._cat is None:
            from virga import paths
            from virga.catalogue import load
            self._cat = load(paths.catalogue(), paths.holdout())
        return self._cat

    @property
    def journal(self):
        from virga import paths
        from virga.journal import Journal
        return Journal(paths.ensure_private(self.store) / "journal.json")

    def _journal_entries(self, fresh: bool = False) -> dict:
        """The journal's entries, re-read (a file read under flock) only when the file changed: every
        SSE viewer asks once a second, the CLI may write it at any time."""
        try:
            mtime = (self.store / "journal.json").stat().st_mtime_ns
        except OSError:
            mtime = None
        seen, j = self._journal_cache
        if fresh or mtime != seen:
            try:
                j = self.journal.entries() if mtime is not None else {}
            except Exception as e:  # noqa: BLE001  (an unreadable journal is a reason to recover, not to crash)
                log.warning("virga live: journal unreadable: %s", e)
                j = {"<unreadable>": {}}
            self._journal_cache = (mtime, j)
        return j

    def ports(self) -> dict:
        if self._ports is None:
            try:
                self._ports = self.ports_fn() or {}
            except Exception:  # noqa: BLE001  (links are best effort)
                self._ports = {}
        return self._ports

    def results(self) -> dict | None:
        p = self.publish / "results.json"
        try:
            m = p.stat().st_mtime
        except OSError:
            return None
        if m != self._results[0]:
            try:
                self._results = (m, json.loads(p.read_text()))
            except (OSError, ValueError):
                self._results = (m, None)
        return self._results[1]

    def _history_path(self) -> Path:
        return self.publish / "virga-live.json"

    def _load_history(self) -> list[dict]:
        try:
            rows = json.loads(self._history_path().read_text()).get("trials") or []
            return [r for r in rows if isinstance(r, dict)][:HISTORY_KEEP]
        except (OSError, ValueError, AttributeError):
            return []

    def _save_history(self) -> None:
        try:
            self.publish.mkdir(parents=True, exist_ok=True)
            tmp = self._history_path().with_suffix(".tmp")
            tmp.write_text(json.dumps({"trials": self.history[:HISTORY_KEEP]}, indent=1))
            tmp.replace(self._history_path())
        except OSError as e:
            log.warning("virga live: could not persist history: %s", e)

    # ---- reads -----------------------------------------------------------------------
    def needs_recovery(self) -> bool:
        return self.enabled and not self._busy and bool(self._journal_entries())

    def catalogue_view(self) -> dict:
        try:
            cat = self.catalogue()
            res = self.results()
            rows = catalogue_rows(cat, selftest_counts(res), self.ports())
        except Exception as e:  # noqa: BLE001
            log.warning("virga live: catalogue unavailable: %s", e)
            rows = []
        return {"enabled": self.enabled, "reason": self.reason, "simulated": self.simulated,
                "hold": {"default_s": HOLD_DEFAULT_S, "max_s": HOLD_MAX_S}, "scenarios": rows}

    def state(self) -> dict:
        with self._mu:
            trial = self._trial
            if trial is not None and not self._busy and time.time() - (trial.get("_t_end") or 0) > TRIAL_LINGER_S:
                self._trial = trial = None
            return {"enabled": self.enabled, "reason": self.reason, "simulated": self.simulated,
                    "busy": self._busy or self._recovering, "needs_recovery": self.needs_recovery(),
                    "trial": public_trial(trial), "recent": copy.deepcopy(self.history[:RECENT_KEEP])}

    def trials_view(self) -> dict:
        res = self.results()
        return {"trials": copy.deepcopy(self.history),
                "selftest": selftest_block(selftest_counts(res), ((res or {}).get("selftest") or {}).get("generated"))}

    def drill(self) -> dict:
        """The public summary's ``drill`` block."""
        with self._mu:
            t = self._trial if self._busy else None
            return {"active": t is not None, "scenario": t["scenario"] if t else None,
                    "title": t["title"] if t else None, "phase": t["phase"] if t else None,
                    "since": t["t_start"] if t else None}

    # ---- writes ----------------------------------------------------------------------
    def _check_scenario(self, scenario: str, hold_s: int | None) -> tuple[Any, dict, int]:
        cat = self.catalogue()
        sc = cat.scenarios.get(scenario)
        if sc is None or scenario in cat.holdout_ids:
            raise Refused(422, "not_in_demo_set", f"{scenario!r} is not in the demo set")
        ok, why = demo_check(sc, cat.holdout_ids)
        if not ok:
            raise Refused(422, "not_in_demo_set", f"{scenario} is not in the demo set: {why}")
        hold = HOLD_DEFAULT_S if hold_s is None else int(hold_s)
        if not HOLD_MIN_S <= hold <= HOLD_MAX_S:
            raise Refused(422, "bad_hold", f"hold_s must be between {HOLD_MIN_S} and {HOLD_MAX_S} seconds")
        return cat, sc, hold

    def _lease_conflict(self, nodes: list[str]) -> list[str]:
        try:
            held = self.holders_fn()
        except Exception as e:  # noqa: BLE001  (the lease itself still protects the write)
            log.warning("virga live: lease table unreadable: %s", e)
            return []
        return sorted({h["device"] for h in held if h.get("device") in nodes})

    def start(self, scenario: str, hold_s: int | None, caller: dict) -> str:
        if not self.enabled:
            raise Refused(403, "off", self.reason or OFF_REASON)
        cat, sc, hold = self._check_scenario(scenario, hold_s)
        with self._mu:
            if self._busy or self._recovering:
                raise Refused(409, "busy", "a fault drill is already running; one at a time")
            if self._journal_entries(fresh=True):
                raise Refused(409, "needs_recovery", "the journal holds an unfinished trial: press Recover first")
            nodes = nodes_of(sc)
            held = self._lease_conflict(nodes)
            if held:
                raise Refused(409, "lease_held", f"another change holds the device lease on {', '.join(held)}")
            now = time.time()
            tid = f"{sc['id']}-live-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:4]}"
            self._trial = new_trial(tid, sc, caller.get("role") or "operator", links_of(sc, self.ports()), now)
            self._trial.update(_caller=caller, _hold_s=hold, _audited=False, _t_end=None)
            self._busy = True
            self._revert = threading.Event()
            self._thread = threading.Thread(target=self._run, args=(cat, sc, hold, tid, caller),
                                            name=f"virga-live-{tid}", daemon=True)
            self._thread.start()
        return tid

    def revert(self, trial_id: str, caller: dict) -> None:
        with self._mu:
            t = self._trial
            if t is None or t["id"] != trial_id:
                raise Refused(404, "not_found", "no such trial")
            if not self._busy:
                raise Refused(409, "finished", "this trial has already finished")
            self._revert.set()
        audit("virga_revert", caller, {"trial_id": trial_id, "scenario": t["scenario"], "nodes": t["nodes"]}, "ok")

    def recover(self, caller: dict) -> dict:
        """Runs ``virga.recover`` synchronously (call it in a worker thread). A node whose commit
        confirmed is still pending after ``RECOVER_PENDING_WAIT_S`` is left alone (the device rolls it
        back at its TTL) and the entry stays: ``ok`` is false, press Recover again later."""
        if not self.enabled:
            raise Refused(403, "off", self.reason or OFF_REASON)
        with self._mu:
            if self._busy or self._recovering:
                raise Refused(409, "busy", "a fault drill or a recovery is running")
            entries = self._journal_entries(fresh=True)
            nodes = sorted({n for e in entries.values() for n in (e.get("write_nodes") or e.get("nodes") or [])})
            held = self._lease_conflict(nodes)
            if held:
                raise Refused(409, "lease_held", f"another change holds the device lease on {', '.join(held)}")
            self._recovering = True
        before = list(entries)
        args = {"trial_ids": before, "nodes": nodes}
        try:
            if not before:
                audit("virga_recover", caller, args, "ok")
                return {"recovered": [], "ok": True}
            rc = self._recover(caller)
            left = self._journal_entries(fresh=True)
            done = [k for k in before if k not in left]
            audit("virga_recover", caller, {**args, "recovered": done}, "ok" if rc == 0 else "residue")
            return {"recovered": done, "ok": rc == 0 and not left}
        except Exception as e:  # noqa: BLE001
            audit("virga_recover", caller, args, f"error:{type(e).__name__}")
            log.exception("virga live: recover failed")
            return {"recovered": [], "ok": False}
        finally:
            with self._mu:
                self._recovering = False

    def _recover(self, caller: dict) -> int:
        from virga.recover import recover
        from virga.runner import Context
        cat = self.catalogue()
        lab = self.lab_factory(cat, f"virga:live:{caller.get('role') or 'operator'}")
        try:
            ctx = Context(lab=lab, cat=cat, journal=self.journal)
            return (self.recover_fn or recover)(ctx, ctx.journal, RECOVER_PENDING_WAIT_S)
        finally:
            with contextlib.suppress(Exception):
                lab.drop()

    # ---- the worker ------------------------------------------------------------------
    def _run(self, cat: Any, sc: dict, hold: int, tid: str, caller: dict) -> None:
        from virga.runner import Context, execute
        rec: dict | None = None
        lab = None
        try:
            lab = self.lab_factory(cat, f"virga:live:{caller.get('role') or 'operator'}")
            ctx = Context(lab=lab, cat=cat, journal=self.journal, mode="demo", hold_s=float(hold),
                          revert_now=self._revert, on_event=lambda ev: self._on_event(tid, ev))
            rec = (self.execute_fn or execute)(ctx, sc, trial_id=tid)
        except Exception as e:  # noqa: BLE001  (lease busy, NETCONF down, a bug: the trial fails, the daemon lives)
            log.exception("virga live trial %s failed", tid)
            rec = {"trial_id": tid, "scenario": sc["id"], "mode": "demo", "verdict": "fail",
                   "error": f"{type(e).__name__}: {str(e)[:400]}", "phases": (rec or {}).get("phases", {})}
            self._on_event(tid, {"t": time.time(), "type": "log", "msg": f"trial stopped: {type(e).__name__}"})
            with self._mu:
                for p in (self._trial or {}).get("phases", []):
                    if p["status"] in ("pending", "running"):
                        p.update(status="failed" if p["status"] == "running" else "skipped", t_end=time.time(),
                                 detail=p["detail"] or f"stopped: {type(e).__name__}")
            self._on_event(tid, {"t": time.time(), "type": "done", "verdict": "fail", "error": type(e).__name__})
        finally:
            if lab is not None:
                with contextlib.suppress(Exception):
                    lab.drop()
        self._finish(tid, rec)

    def _on_event(self, tid: str, ev: dict) -> None:
        audit_now: tuple | None = None
        with self._mu:
            t = self._trial
            if t is None or t["id"] != tid:
                return
            now = ev.get("t") or time.time()
            kind = ev.get("type")
            if kind == "log":
                t["log"] = (t["log"] + [{"t": now, "msg": public_line(ev.get("msg") or "")}])[-LOG_KEEP:]
            elif kind == "phase":
                p = next((x for x in t["phases"] if x["name"] == ev.get("name")), None)
                if p is None:
                    return
                status = ev.get("status")
                if status == "running":
                    p.update(status="running", t_start=now, t_end=None)
                    t["phase"] = p["name"]
                else:
                    p.update(status=status, t_end=now, t_start=p["t_start"] or (now if status != "skipped" else None))
                if ev.get("detail"):
                    p["detail"] = scrub(ev["detail"])
                if p["name"] == "hold" and ev.get("hold_until"):
                    t["hold_until"] = float(ev["hold_until"])
                if p["name"] == "inject" and status in ("ok", "failed") and not t["_audited"]:
                    outcome = ("changed" if ev.get("changed") else "rejected" if ev.get("rejected")
                               else "no_change" if status == "ok" else f"error:{ev.get('error') or 'inject'}")
                    t["_audited"] = True
                    audit_now = (t, outcome)
            elif kind == "done":
                t["verdict"] = "pass" if ev.get("verdict") == "pass" else "fail"
                t["phase"] = "done"
                t["_t_end"] = now
                if not t["_audited"]:  # blocked at preflight or stopped before the inject finished
                    v = str(ev.get("verdict") or "")
                    t["_audited"] = True
                    audit_now = (t, "blocked" if v.startswith("blocked") else f"error:{ev.get('error') or 'trial'}")
        if audit_now:
            t, outcome = audit_now
            audit("virga_inject", t["_caller"], {"trial_id": t["id"], "scenario": t["scenario"], "nodes": t["nodes"],
                                                 "hold_s": t["_hold_s"], "outcome": outcome}, outcome)

    def _finish(self, tid: str, rec: dict | None) -> None:
        with self._mu:
            t = self._trial
        if t is None or t["id"] != tid:
            return
        if not t.get("_t_end"):
            self._on_event(tid, {"t": time.time(), "type": "done", "verdict": (rec or {}).get("verdict")})
        self._write_private(rec, t)
        opened = False
        if self.incident_check is not None:
            try:
                opened = bool(self.incident_check(t["t_start"], t["_t_end"] or time.time()))
            except Exception:  # noqa: BLE001
                opened = False
        row = summary_row(t, rec, opened)
        with self._mu:
            self.history = [row] + [r for r in self.history if r.get("id") != tid][:HISTORY_KEEP - 1]
            self._busy = False
        self._save_history()

    def _write_private(self, rec: dict | None, t: dict) -> None:
        from virga import paths
        try:
            d = paths.ensure_private(self.store / "live")
            day = datetime.now(timezone.utc).strftime("%Y%m%d")
            row = {**(rec or {}), "live": {"trial_id": t["id"], "by": t["_caller"], "hold_s": t["_hold_s"],
                                           "public": public_trial(t)}}
            with (d / f"live-{day}.jsonl").open("a") as fh:
                fh.write(json.dumps(row, default=str) + "\n")
        except OSError as e:
            log.warning("virga live: could not write the private record: %s", e)


# ---------------------------------------------------------------------- replay: history only

class ReplayVirga:
    """The public replay: never injects (D7). Catalogue and history come from the snapshot when it
    recorded them (``shimpaku snapshot export``), else empty. Also stands in, with another reason,
    when Virga itself cannot be loaded."""

    simulated = False
    enabled = False

    def __init__(self, snapshot: dict | None = None, reason: str = REPLAY_REASON):
        self.snap = (snapshot or {}).get("virga") or {}
        self.reason = reason

    def catalogue_view(self) -> dict:
        cat = self.snap.get("catalogue") or {}
        return {"enabled": False, "reason": self.reason, "simulated": False,
                "hold": {"default_s": HOLD_DEFAULT_S, "max_s": HOLD_MAX_S},
                "scenarios": [{**s, "demo_ok": False, "why_not": self.reason} for s in cat.get("scenarios") or []]}

    def state(self) -> dict:
        trials = (self.snap.get("trials") or {}).get("trials") or []
        return {"enabled": False, "reason": self.reason, "simulated": False, "busy": False,
                "needs_recovery": False, "trial": None, "recent": trials[:RECENT_KEEP]}

    def trials_view(self) -> dict:
        tr = self.snap.get("trials") or {}
        return {"trials": tr.get("trials") or [], "selftest": tr.get("selftest") or selftest_block({})}

    def drill(self) -> dict:
        return {"active": False, "scenario": None, "title": None, "phase": None, "since": None}

    def needs_recovery(self) -> bool:
        return False

    def start(self, *_: Any, **__: Any) -> str:
        raise Refused(403, "off", self.reason)

    revert = recover = start


# ---------------------------------------------------------------------- wiring

def manager_for(app: Any) -> Any:
    """``app.state.virga_live`` (tests set a fake), else the replay's, the canned demo's, or the lab's."""
    m = getattr(app.state, "virga_live", None)
    if m is not None:
        return m
    svc = app.state.service
    if getattr(svc, "REPLAY", False):
        load = getattr(svc, "load", None)
        m = ReplayVirga(load() if callable(load) else None)
    elif getattr(svc, "DEMO", False):
        from shimpaku.api.demo_virga import DemoVirga
        m = DemoVirga()
    else:
        try:
            m = VirgaLive.from_settings()
        except ImportError as e:  # the daemon was installed without bench/virga
            log.warning("virga live unavailable: %s", e)
            m = ReplayVirga(None, reason="Virga is not installed with this daemon")
    app.state.virga_live = m
    return m


def incident_checker(fetch: Callable[[], Any], loop: asyncio.AbstractEventLoop) -> Callable[[float, float], bool]:
    """For the worker thread: did an incident open in [t0, t1]? ``fetch`` is an async incident list."""
    from shimpaku.api import view

    def check(t0: float, t1: float) -> bool:
        if loop.is_closed() or not loop.is_running():
            return False
        out = asyncio.run_coroutine_threadsafe(fetch(), loop).result(timeout=15)
        rows = view.incident_list(out).get("incidents") or [] if isinstance(out, dict) else []
        return any(t0 <= (view.ts(i.get("opened_at")) or 0) <= t1 + 60 for i in rows if isinstance(i, dict))
    return check


def _real_lab(cat: Any, holder: str) -> Any:
    from virga.lab import Lab
    return Lab(cat.lab, holder=holder)


def _real_ports() -> dict:
    from shimpaku import devices
    return {k: e.peer for k, e in devices.inventory().ports.items()}


def _real_holders() -> list[dict]:
    from shimpaku import lock
    from virga import paths
    return lock.holders(db=paths.lock_db())
