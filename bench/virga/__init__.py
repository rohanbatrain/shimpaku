"""Virga — Shimpaku's fault-injection benchmark.

Injects catalogued faults into the Junos lab, proves independently that each took effect, reverts it,
proves the revert line for line, and scores a system under test against R5's scoring contract.
Ported from the R9 research prototype (`aiops-bench/bench.py`).

Run as `python -m virga` with `bench/` on PYTHONPATH (see docs/build/bench.md).
"""

__version__ = "0.1.0"

RUN_SCHEMA = "virga/run/v1"
PLAN_SCHEMA = "virga/plan/v1"
RESULTS_SCHEMA = "virga/results/v1"
