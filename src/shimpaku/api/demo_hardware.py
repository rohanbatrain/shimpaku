"""Demo data for the hardware twin (``/api/hardware``) when ``SHIMPAKU_DEMO=1``.

Canned and consistent with ``api/demo.py`` (inventory version 26.2R1.7, uptimes of three days and
a few hours) and ``api/demo_live.py`` (port rates). The guests are named the way the lab host names
them (``lab-<device>``); the host sample reuses ``api/demo_ops.py``. Every payload that reaches the
page is marked ``demo: true`` by the router.
"""

from __future__ import annotations

import time

DEMO = True
_NODES = [("r1", "vjunos-router"), ("r2", "vjunos-router"), ("sw1", "vjunos-switch"), ("spine1", "vjunos-switch"),
          ("spine2", "vjunos-switch"), ("leaf1", "vjunos-switch"), ("leaf2", "vjunos-switch")]


def declared() -> dict[str, dict]:
    return {n: {"vcpu": 4, "memory_mb": 5120} for n, _ in _NODES}


async def device_get_facts(device: str) -> dict:
    names = [n for n, _ in _NODES]
    if device not in names:
        return {"available": False, "reason": "unknown device"}
    i = names.index(device)
    up = 86400 * 3 + i * 3600 + int(time.time() % 3600)
    return {"demo": True, "device": device, "version": "26.2R1.7", "model": dict(_NODES)[device],
            "uptime_s": up, "booted_at": time.time() - up}


def vms() -> dict:
    now = time.time()
    return {"vms": [{"id": str(i + 1), "name": f"lab-{n}", "state": "running"} for i, (n, _) in enumerate(_NODES)]
            + [{"id": None, "name": "unrelated-vm", "state": "shut off"}],
            "error": None, "t": now - (now % 15)}


def host_latest() -> dict:
    from shimpaku.api import demo_ops
    latest = dict(demo_ops.host()["latest"])
    latest["vms_running"] = len(_NODES)
    return latest
