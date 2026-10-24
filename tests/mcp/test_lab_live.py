"""Read-only live checks (run on the lab host: `pytest -m lab tests/mcp`). No writes."""

from __future__ import annotations

import pytest

from shimpaku.config import settings

pytestmark = [pytest.mark.lab,
              pytest.mark.skipif(not all(p.exists() for p in settings.topology_paths()),
                                 reason="not on the lab host (topology TOMLs absent)")]


def test_port_facts_live_roles():
    from shimpaku import devices
    f = devices.port_config("spine2", "ge-0/0/0")
    assert f["live_role"] == "fabric" and f["topology_role"] == "fabric" and f["lldp_neighbor"]
    u = devices.port_config("spine2", "ge-0/0/9")
    assert u["live_role"] == "unused" and not u["configured"]


def test_routing_health_live():
    from shimpaku import devices
    h = devices.routing_health("leaf2")["devices"][0]
    assert sum(1 for n in h["ospf"] if n["state"] == "Full") == 2
