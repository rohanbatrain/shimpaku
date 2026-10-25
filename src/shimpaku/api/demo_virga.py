"""Canned fault lab for the dashboard in demo mode (``SHIMPAKU_DEMO=1``): a SIMULATED Virga trial.

Same methods and payloads as ``shimpaku.virga_live.VirgaLive``, every payload ``simulated: true``,
nothing touches a device or the journal. An "injected" trial walks the phases on the clock over about
a minute (the hold is capped at 35 s here; *Revert now* ends it early), so the Fault lab page, the
drill banner and the status health can be developed and screenshotted without the lab. The
catalogue is the repo's real one (``bench/catalogue.yaml``) with canned self-test counts.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from shimpaku import virga_live as vl

HOLD_CAP_S = 35  # keeps a simulated trial near 60 s whatever hold the page asks for
# (phase, seconds) before and after the hold
BEFORE = [("preflight", 4.0), ("inject", 3.0), ("verify_injected", 7.0)]
AFTER = [("revert", 4.0), ("verify_reverted", 8.0)]
DETAIL = {
    "preflight": "baseline healthy: 9 probes in 3.8 s",
    "inject": "1 commit(s) on {node}, commit confirmed {ttl} min",
    "verify_injected": "fault confirmed by 6 probe(s), time to effect 2.4 s",
    "hold": "ended by {who} after {s:.0f} s; fault present in {n} of {n} samples",
    "revert": "1 commit(s)",
    "verify_reverted": "healthy again in 7.6 s; configuration identical to the snapshot (412 lines)",
}
# demo topology ports (api/demo.py topo_get_neighbors), for the links of each scenario
PORTS = {("r1", "ge-0/0/1"): "sw1", ("sw1", "ge-0/0/0"): "r1", ("r1", "ge-0/0/0"): "r2", ("r2", "ge-0/0/0"): "r1",
         ("leaf1", "ge-0/0/0"): "spine1", ("spine1", "ge-0/0/0"): "leaf1", ("leaf1", "ge-0/0/1"): "spine2",
         ("spine2", "ge-0/0/0"): "leaf1", ("leaf2", "ge-0/0/0"): "spine1", ("spine1", "ge-0/0/1"): "leaf2",
         ("leaf2", "ge-0/0/1"): "spine2", ("spine2", "ge-0/0/1"): "leaf2"}


def _canned_results(cat: Any) -> dict:
    sc = {sid: {"passes": 5, "trials": 5, "last": "2026-10-24T18:02:11.000Z", "time_to_effect_s_median": 2.4,
                "time_to_restore_s_median": 7.6} for sid, s in cat.scenarios.items() if s.get("status") == "selftested"}
    return {"selftest": {"generated": "2026-10-24T18:05:00+00:00", "scenarios": sc}}


class DemoVirga:
    simulated = True
    enabled = True
    reason = None

    def __init__(self, clock=time.time):
        self.clock = clock
        self._mu = threading.Lock()
        self._trial: dict | None = None     # {id, sc, hold_s, t0, revert_at, role}
        self._cat = None
        now = clock()
        self.history = [
            {"id": "FAB-01-live-demo-a", "scenario": "FAB-01", "title": "Fabric link down (local admin disable, carrier lost)",
             "t_start": now - 3600, "t_end": now - 3540, "verdict": "pass", "time_to_effect_s": 0.6,
             "time_to_restore_s": 9.1, "incident_opened": True},
            {"id": "CAM-07-live-demo-b", "scenario": "CAM-07", "title": "OSPF r1-r2 adjacency lost (interface made passive)",
             "t_start": now - 7200, "t_end": now - 7010, "verdict": "pass", "time_to_effect_s": 3.9,
             "time_to_restore_s": 12.4, "incident_opened": True},
        ]

    def catalogue(self) -> Any:
        if self._cat is None:
            from virga import paths
            from virga.catalogue import load
            self._cat = load(paths.catalogue())  # the repo file only: no private holdout in a demo
        return self._cat

    # ---- the simulated timeline --------------------------------------------------------------
    def _timeline(self, t: dict) -> list[tuple[str, float, float]]:
        out, at = [], t["t0"]
        for name, d in BEFORE:
            out.append((name, at, at + d))
            at += d
        hold_end = at + min(t["hold_s"], HOLD_CAP_S)
        if t["revert_at"] is not None:
            hold_end = max(at, min(hold_end, t["revert_at"]))
        out.append(("hold", at, hold_end))
        at = hold_end
        for name, d in AFTER:
            out.append((name, at, at + d))
            at += d
        return out

    def _view(self, t: dict, now: float) -> tuple[dict, bool]:
        sc = t["sc"]
        trial = vl.new_trial(t["id"], sc, t["role"], vl.links_of(sc, PORTS), t["t0"])
        tl = self._timeline(t)
        node = (trial["nodes"] or ["leaf1"])[0]
        from virga.runner import demo_ttl_min
        ttl = demo_ttl_min(t["hold_s"])
        logs = [{"t": t["t0"], "msg": f"=== {sc['id']} {sc['title']}  trial {t['id']}  (confirmed) [simulated]"}]
        for p, (name, a, b) in zip(trial["phases"], tl):
            if now < a:
                break
            trial["phase"] = name
            p.update(status="running", t_start=a)
            if name == "hold":
                trial["hold_until"] = a + t["hold_s"]
            if now >= b:
                who = "the operator" if t["revert_at"] is not None and t["revert_at"] < b + 0.01 else "the timer"
                p.update(status="ok", t_end=b, detail=DETAIL[name].format(node=node, ttl=ttl, who=who, s=b - a,
                                                                       n=max(1, int((b - a) // 4))))
                logs.append({"t": b, "msg": f"    {name} ok [simulated]"})
        done = now >= tl[-1][2]
        if done:
            trial.update(phase="done", verdict="pass")
            logs.append({"t": tl[-1][2], "msg": "    verdict PASS [simulated]"})
        trial["log"] = logs[-vl.LOG_KEEP:]
        trial["_t_end"] = tl[-1][2]
        return trial, done

    def _settle(self, now: float) -> tuple[dict | None, bool]:
        """The current trial's view; a finished one is moved into the history once."""
        t = self._trial
        if t is None:
            return None, False
        trial, done = self._view(t, now)
        if done and not t.get("filed"):
            t["filed"] = True
            self.history.insert(0, vl.summary_row(trial, {"phases": {"verify_injected": {"time_to_effect_s": 2.4},
                                                                     "verify_reverted": {"time_to_restore_s": 7.6}}},
                                                  True))
        if done and now - trial["_t_end"] > vl.TRIAL_LINGER_S:
            self._trial = None
            return None, False
        return trial, not done

    # ---- the VirgaLive surface -----------------------------------------------------------------
    def needs_recovery(self) -> bool:
        return False

    def catalogue_view(self) -> dict:
        try:
            cat = self.catalogue()
            rows = vl.catalogue_rows(cat, vl.selftest_counts(_canned_results(cat)), PORTS)
        except Exception:  # noqa: BLE001  (virga not importable: an empty catalogue, still simulated)
            rows = []
        return {"enabled": True, "reason": None, "simulated": True,
                "hold": {"default_s": vl.HOLD_DEFAULT_S, "max_s": vl.HOLD_MAX_S}, "scenarios": rows}

    def state(self) -> dict:
        with self._mu:
            trial, busy = self._settle(self.clock())
            return {"enabled": True, "reason": None, "simulated": True, "busy": busy, "needs_recovery": False,
                    "trial": vl.public_trial(trial), "recent": [dict(r) for r in self.history[:vl.RECENT_KEEP]]}

    def trials_view(self) -> dict:
        with self._mu:
            self._settle(self.clock())
            cat = self.catalogue()
            return {"trials": [dict(r) for r in self.history], "simulated": True,
                    "selftest": vl.selftest_block(vl.selftest_counts(_canned_results(cat)), "2026-10-24T18:05:00+00:00")}

    def drill(self) -> dict:
        with self._mu:
            trial, busy = self._settle(self.clock())
            if not busy:
                return {"active": False, "scenario": None, "title": None, "phase": None, "since": None}
            return {"active": True, "scenario": trial["scenario"], "title": trial["title"], "phase": trial["phase"],
                    "since": trial["t_start"]}

    def start(self, scenario: str, hold_s: int | None, caller: dict) -> str:
        cat = self.catalogue()
        sc = cat.scenarios.get(scenario)
        if sc is None:
            raise vl.Refused(422, "not_in_demo_set", f"{scenario!r} is not in the demo set")
        ok, why = vl.demo_check(sc, cat.holdout_ids)
        if not ok:
            raise vl.Refused(422, "not_in_demo_set", f"{scenario} is not in the demo set: {why}")
        hold = vl.HOLD_DEFAULT_S if hold_s is None else int(hold_s)
        if not vl.HOLD_MIN_S <= hold <= vl.HOLD_MAX_S:
            raise vl.Refused(422, "bad_hold", f"hold_s must be between {vl.HOLD_MIN_S} and {vl.HOLD_MAX_S} seconds")
        with self._mu:
            _, busy = self._settle(self.clock())
            if busy:
                raise vl.Refused(409, "busy", "a fault drill is already running; one at a time")
            tid = f"{scenario}-live-demo-{uuid.uuid4().hex[:6]}"
            self._trial = {"id": tid, "sc": sc, "hold_s": hold, "t0": self.clock(), "revert_at": None,
                           "role": caller.get("role") or "operator"}
        vl.audit("virga_inject", caller, {"trial_id": tid, "scenario": scenario, "nodes": vl.nodes_of(sc),
                                          "hold_s": hold, "outcome": "simulated"}, "simulated")
        return tid

    def revert(self, trial_id: str, caller: dict) -> None:
        with self._mu:
            t = self._trial
            if t is None or t["id"] != trial_id:
                raise vl.Refused(404, "not_found", "no such trial")
            _, busy = self._settle(self.clock())
            if not busy:
                raise vl.Refused(409, "finished", "this trial has already finished")
            if t["revert_at"] is None:
                t["revert_at"] = self.clock()

    def recover(self, caller: dict) -> dict:
        return {"recovered": [], "ok": True}
