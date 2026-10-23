# Shimpaku

A Mist-style assurance NMS for one Junos home lab, agent-first. **Shim** is its assistant.
Independent learning project; not affiliated with HPE or Juniper Networks.

Status: MVP phase 0. The plan, research and review live in the private lab book
(`research/2026-10-24-mist-emulation/PLAN.md`).

| Module | What |
| --- | --- |
| `src/shimpaku/lock.py` | one writer per device across processes and Unix users (SQLite lease + pending-`commit confirmed` refusal) |
| `ops/phase0-root.sh` | the operator's one-time root steps on the lab host |
