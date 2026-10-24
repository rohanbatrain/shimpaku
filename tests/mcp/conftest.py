"""Registers the `lab` marker and keeps live-lab tests out of the default run.

CONTRACT.md: "pytest; no live lab in unit tests; live checks behind `-m lab`". Without this,
`pytest tests/mcp` would run `test_lab_live.py` against whatever real device state <lab-host> happens
to be in at that moment (other builders' and the bench runner's writes included), which is
exactly the flakiness the contract is avoiding. `pytest -m lab tests/mcp` still runs them.
"""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "lab: live checks against the real lab (run with `-m lab`)")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if "lab" in (config.option.markexpr or ""):
        return  # the operator explicitly asked for `-m lab` (or `-m "lab and ..."`)
    skip_lab = pytest.mark.skip(reason="live lab check; run explicitly with `pytest -m lab`")
    for item in items:
        if "lab" in item.keywords:
            item.add_marker(skip_lab)
