"""`virga recover`: put back everything the journal says is not proven reverted.

Per entry: (1) wait for any `commit confirmed` on its nodes to lapse or be confirmed (a confirmed
inject reverts itself; writing earlier would confirm the fault, R9 §2.3); (2) lease the nodes;
(3) revert any applied decoys, newest first; (4) apply the inverse steps (idempotent; `auto` steps
are skipped since the device has already rolled back); (5) compare each node's config with the
pre-inject snapshot line for line. Only an identical config clears the entry; a residue is printed
and the entry kept (exit code 1), so a human can decide.
"""

from __future__ import annotations

from virga.journal import Journal
from virga.runner import Context, config_diff, log, run_steps, wait_no_pending


def recover(ctx: Context, journal: Journal, pending_wait_s: float | None = None) -> int:
    lab = ctx.lab
    entries = journal.entries()
    if not entries:
        log("journal empty: nothing to recover")
        return 0
    bad = 0
    for key, e in entries.items():
        nodes = e.get("nodes", [])
        write = e.get("write_nodes", nodes)
        log(f"recover {key}: {e.get('scenario')} on {nodes} ({e.get('inject_mode')}), decoys {len(e.get('decoys', []))}")
        wait_s = pending_wait_s if pending_wait_s is not None else 60 * int(e.get("ttl_min", 5)) + 150
        decoy_nodes = sorted({d["node"] for d in e.get("decoys", [])})
        pend = wait_no_pending(lab, sorted(set(nodes) | set(decoy_nodes)), wait_s)
        if any(v is True for v in pend.values()):
            log(f"    still pending after {wait_s} s: {pend} -- someone else's commit? left for a human")
            bad += 1
            continue
        try:
            with lab.leased(sorted(set(write) | set(decoy_nodes)), check_pending=True):
                for d in reversed(e.get("decoys", [])):
                    lab.commit(d["node"], d["revert"], 2, confirm_after=True)
                    journal.pop_item(key, "decoys", {"decoy_id": d["decoy_id"]})
                steps = [s for s in e.get("revert", []) if "auto" not in s]
                ctx.inject_mode = "confirmed"
                run_steps(ctx, steps, "revert", 2, [])
                residue = {}
                for n, snap in (e.get("snapshot") or {}).items():
                    d = config_diff(snap, lab.config_lines(n))
                    if d:
                        residue[n] = d
                pend = {n: lab.pending(n) for n in nodes}
        except Exception as ex:
            log(f"    recover FAILED: {type(ex).__name__}: {ex}")
            bad += 1
            continue
        if residue or any(pend.values()):
            log(f"    NOT clean: residue={residue} pending={pend}; entry kept")
            bad += 1
        else:
            journal.remove(key)
            log(f"    clean: config identical to the pre-inject snapshot "
                f"({sum(len(v) for v in (e.get('snapshot') or {}).values())} lines); entry removed")
    return 1 if bad else 0
