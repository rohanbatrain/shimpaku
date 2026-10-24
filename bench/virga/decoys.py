"""Decoy commits ("change noise", REVIEW B3 / PLAN §7).

The last commit's diff on the faulted node IS the root cause, so a system that blames the newest
commit (baseline B0b) would look clever. During bench runs Virga makes harmless commits on its write
allocation at a seeded random rate, with the same neutral comment as the injects, so the newest commit
is often not the fault. Each decoy is a pair (apply, revert); applied decoys are journaled and are
reverted before the trial's config-identity check, so they never count as residue.

Rules: a decoy never lands on a node with a pending `commit confirmed` (it would confirm it), and on a
trial's own nodes only when that trial injected by plain commit (run B). In confirmed mode the decoy is
committed `commit confirmed` and confirmed at once, so commit history looks the same for both.
"""

from __future__ import annotations

import random
import time

from virga.catalogue import subst


class DecoyGen:
    def __init__(self, lab_spec: dict, seed: int, rate_per_trial: float = 1.5):
        spec = lab_spec.get("decoys") or {}
        self.kinds = spec.get("kinds", [])
        self.words = spec.get("words", ["spare"])
        self.rng = random.Random(f"decoy-{seed}")
        self.rate = rate_per_trial
        self.n = 0

    def schedule(self, t_start: float, window_s: float, trial_nodes: list[str], plain: bool) -> list[dict]:
        """Draw this trial's decoys: count ~ Poisson(rate), times uniform in the window."""
        if not self.kinds:
            return []
        count, p, L = 0, 1.0, pow(2.718281828, -self.rate)
        while True:
            p *= self.rng.random()
            if p <= L:
                break
            count += 1
        out = []
        for _ in range(count):
            kind = self.rng.choice(self.kinds)
            allowed = [n for n in kind["nodes"] if plain or n not in trial_nodes]
            if not allowed:
                continue
            node = self.rng.choice(allowed)
            self.n += 1
            env = {"word": self.rng.choice(self.words), "n": str(self.rng.randint(10, 250))}
            out.append({"decoy_id": f"d{self.n:04d}", "node": node, "t_at": t_start + self.rng.uniform(5, max(6, window_s - 30)),
                        "apply": subst(kind["apply"], env), "revert": subst(kind["revert"], env)})
        return sorted(out, key=lambda d: d["t_at"])


def apply(lab, journal, key: str, d: dict, confirm_min: int | None) -> dict:
    """Apply one decoy under the device lease; skipped (not an error) when the node is busy/pending."""
    from shimpaku.lock import DeviceBusy, PendingConfirm

    try:
        journal.append(key, "decoys", {"decoy_id": d["decoy_id"], "node": d["node"], "revert": d["revert"]})
        with lab.leased([d["node"]], check_pending=True, wait=20):
            r = lab.commit(d["node"], d["apply"], confirm_min, confirm_after=bool(confirm_min))
        return {"decoy_id": d["decoy_id"], "node": d["node"], "t": time.time(), "changed": r.get("changed"), "applied": True}
    except (DeviceBusy, PendingConfirm) as e:
        journal.pop_item(key, "decoys", {"decoy_id": d["decoy_id"]})
        return {"decoy_id": d["decoy_id"], "node": d["node"], "applied": False, "skipped": type(e).__name__}


def revert_all(lab, journal, key: str, applied: list[dict]) -> list[dict]:
    """Revert decoys newest first; each revert is confirmed on a fresh session."""
    out = []
    for d in reversed(applied):
        with lab.leased([d["node"]], check_pending=False):
            r = lab.commit(d["node"], d["revert"], 2, confirm_after=True)
        journal.pop_item(key, "decoys", {"decoy_id": d["decoy_id"]})
        out.append({"decoy_id": d["decoy_id"], "node": d["node"], "changed": r.get("changed")})
    return out
