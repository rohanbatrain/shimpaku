"""The one service API every surface calls (CONTRACT "Service layer"; owner B-mcp).

Consumers: `shimpaku.api` (REST GETs + approve/cancel), `shimpaku.mcp` (T0 tools 1:1, action
tools), `shimpaku.assistant` (through MCP). Every function is `async` and returns a plain
JSON-serialisable dict. Errors are returned, not raised, as `{"error": {"code", "message"}}`
— except `action_approve`, which raises `PermissionError` for a non-dashboard caller so the
API turns it into a 403.

Analytics and telemetry data (SLE, incidents, blast radius, probes, bench results) belong to
other builders. They plug in with `register_provider(name, fn)` — `fn` may be sync or async and
takes the same keyword arguments. Until a provider is registered the function answers
`{"available": False, ...}` so callers can render "not yet collected" instead of failing.

Device reads go through `shimpaku.devices` (PyEZ pool) in a worker thread.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any, Callable, Literal

from shimpaku import devices

ProbeKind = Literal["ping", "dhcp", "dns", "http", "arp"]

_providers: dict[str, Callable[..., Any]] = {}
_audit = None  # shimpaku.mcp.audit.AuditLog, set by set_audit_log()


def register_provider(name: str, fn: Callable[..., Any]) -> None:
    """Plug a data source in: names are the service function names below (e.g. 'sle_get_summary',
    'incident_list', 'incident_get', 'sle_get_blast_radius', 'probe_run', 'bench_results',
    'topology'). Later registration wins."""
    _providers[name] = fn


def set_audit_log(log) -> None:
    global _audit
    _audit = log


def _err(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


async def _call_provider(name: str, **kw) -> dict:
    fn = _providers.get(name)
    if fn is None:
        return {"available": False, "reason": f"{name}: no data source registered yet"}
    try:
        out = fn(**kw)
        if inspect.isawaitable(out):
            out = await out
        return out
    except Exception as e:  # noqa: BLE001
        return _err("provider_failed", f"{name}: {type(e).__name__}: {e}")


async def _device_read(fn: Callable[..., dict], *a, **kw) -> dict:
    try:
        return await asyncio.to_thread(fn, *a, **kw)
    except devices.UnknownDevice as e:
        return _err("unknown_device", str(e).strip("'\""))
    except ValueError as e:
        return _err("bad_argument", str(e))
    except Exception as e:  # noqa: BLE001
        return _err("device_unreachable", f"{type(e).__name__}")


# ===================================================================== T0 reads

async def inventory_list(site: str | None = None) -> dict:
    """Nodes from the lab TOMLs: `{"nodes": [{name, site, kind, mgmt_ip, topology}], "sites": [...]}`."""
    inv = devices.inventory()
    nodes = [n.__dict__ for n in inv.nodes.values() if site in (None, n.site)]
    return {"nodes": nodes, "sites": sorted({n.site for n in inv.nodes.values()})}


async def sle_get_summary(site: str, window_min: int = 60) -> dict:
    """SLE per classifier for a site over the window; every percentage carries its n
    (probe-minutes / device-minutes, REVIEW B1). Provider: B-analytics."""
    return await _call_provider("sle_get_summary", site=site, window_min=int(window_min))


async def sle_get_blast_radius(incident_id: str) -> dict:
    """Blast radius of an incident (scope, statistics, abstention). Provider: B-analytics."""
    return await _call_provider("sle_get_blast_radius", incident_id=incident_id)


async def iface_get_errors(device: str, ifname: str | None = None) -> dict:
    """Error/drop counters per physical port (live, PyEZ `get-interface-information extensive`):
    `{"device", "interfaces": [{ifname, admin, oper, input_errors, fcs_errors, ...}]}`."""
    return await _device_read(devices.iface_errors, device, ifname)


async def port_get_config(device: str, ifname: str) -> dict:
    """One port: own config, inherited config (apply-groups / interface-range expanded), VLAN
    membership, LLDP neighbour, live role and the topology's intended role."""
    return await _device_read(devices.port_config, device, ifname)


async def topo_get_neighbors(device: str | None = None) -> dict:
    """LLDP neighbours (live) and the intended links from the lab TOML."""
    prov = _providers.get("topology")
    if prov is not None:
        return await _call_provider("topology", device=device)
    return await _device_read(devices.lldp_neighbors, device)


async def routing_get_health(device: str | None = None) -> dict:
    """OSPF neighbours, BGP peers and BFD sessions per device (live)."""
    return await _device_read(devices.routing_health, device)


async def config_get_commits(device: str, n: int = 10) -> dict:
    """Newest commits (index, user, client, time, log, pending-confirm flag)."""
    return await _device_read(devices.commits, device, n)


async def config_get_diff(device: str, rollback: int = 1) -> dict:
    """`show system rollback N compare 0`: what the last N commits changed."""
    return await _device_read(devices.config_diff, device, rollback)


async def probe_run(target: str, kind: ProbeKind = "ping") -> dict:
    """Run a synthetic probe now (reads the network). Provider: B-telemetry / farm."""
    return await _call_provider("probe_run", target=target, kind=kind)


async def incident_list(status: str | None = None) -> dict:
    """Incidents, newest first: `{"incidents": [...]}`. Provider: B-analytics."""
    return await _call_provider("incident_list", status=status)


async def incident_get(incident_id: str) -> dict:
    """One incident with evidence, classifier and linked actions. Provider: B-analytics."""
    out = await _call_provider("incident_get", incident_id=incident_id)
    if isinstance(out, dict) and "error" not in out and out.get("available", True):
        out.setdefault("actions", [a for a in (await action_list()).get("actions", [])
                                   if a.get("incident_id") == incident_id])
    return out


async def audit_query(tool: str | None = None, action_id: str | None = None, since: float | None = None,
                      status: str | None = None, limit: int = 100) -> dict:
    """Audit rows (newest first), hash chain included; `verify` tells whether the chain holds."""
    if _audit is None:
        return {"available": False, "reason": "audit log not initialised"}
    rows = await asyncio.to_thread(_audit.query, tool, action_id, since, status, limit)
    return {"rows": rows}


async def audit_verify() -> dict:
    """Recompute the audit hash chain (SQLite and JSONL)."""
    if _audit is None:
        return {"available": False, "reason": "audit log not initialised"}
    return await asyncio.to_thread(_audit.verify)


async def bench_results() -> dict:
    """The benchmark scorer's published output only (never the catalogue or ground truth)."""
    return await _call_provider("bench_results")


# ===================================================================== actions

def _engine():
    from shimpaku.actions.engine import engine
    return engine()


async def action_propose(kind: str, args: dict, reason: str, caller: Any) -> dict:
    """Propose an action. Policy (G0–G9) runs in code now; the result is the stored action with
    `status` = `blocked` (with `policy.reasons`), `awaiting_approval`, or `automatic` (T1 with
    self-driving on). Nothing executes from here — the executor picks up approved work.
    `caller` is a `shimpaku.actions.engine.Caller` (or dict) built by the transport."""
    try:
        return await asyncio.to_thread(_engine().propose, kind, args, reason, caller)
    except Exception as e:  # noqa: BLE001
        return _err("propose_failed", f"{type(e).__name__}: {e}")


def _normalise_action_error(a: dict) -> dict:
    """The executor stores a plain diagnostic string under `error` on a failed action (G1/G5/...
    re-check messages); the top-level service `error` field is always a `{code, message}` dict
    (e.g. `not_found`). Both share the key name, so an action's string must be wrapped before it
    reaches a typed caller (the MCP `ActionResult` model rejects a bare string and raises)."""
    if isinstance(a.get("error"), str):
        a = {**a, "error": {"code": "execution_failed", "message": a["error"]}}
    return a


async def action_get(action_id: str) -> dict:
    """One action with its timeline, commits (Junos comment `shimpaku <id> …`) and validation."""
    a = await asyncio.to_thread(_engine().get, action_id)
    return _normalise_action_error(a) if a is not None else _err("not_found", f"no action {action_id!r}")


async def action_list(status: str | None = None) -> dict:
    """Actions, newest first, optionally filtered by status."""
    items = await asyncio.to_thread(_engine().list, status)
    return {"actions": [_normalise_action_error(a) for a in items]}


async def action_approve(action_id: str, user: Any) -> dict:
    """Approve an `awaiting_approval` action. Dashboard session only: `user` must be a Caller
    with kind="dashboard" (the API builds it from the session). Raises PermissionError otherwise;
    returns `{"error": ...}` for an unknown action or wrong state."""
    from shimpaku.actions.engine import ApprovalRefused
    from shimpaku.actions.state import StateError

    try:
        return await asyncio.to_thread(_engine().approve, action_id, user)
    except ApprovalRefused:
        raise
    except StateError as e:
        return _err("bad_state", str(e))
    except KeyError:
        return _err("not_found", f"no action {action_id!r}")


async def action_reject(action_id: str, user: Any) -> dict:
    """Reject an `awaiting_approval` action (dashboard session only)."""
    from shimpaku.actions.engine import ApprovalRefused
    from shimpaku.actions.state import StateError

    try:
        return await asyncio.to_thread(_engine().reject, action_id, user)
    except ApprovalRefused:
        raise
    except (StateError, KeyError) as e:
        return _err("bad_state", str(e))


async def action_cancel(action_id: str, caller: Any) -> dict:
    """Cancel an action that has not started running (proposer or dashboard user)."""
    from shimpaku.actions.engine import ApprovalRefused
    from shimpaku.actions.state import StateError

    try:
        return await asyncio.to_thread(_engine().cancel, action_id, caller)
    except ApprovalRefused as e:
        return _err("forbidden", str(e))
    except StateError as e:
        return _err("bad_state", str(e))
    except KeyError:
        return _err("not_found", f"no action {action_id!r}")


async def policy_get() -> dict:
    """The guardrail table and the live policy values (read-only; `lab://policy`)."""
    from shimpaku.actions.policy import CATALOGUE
    from shimpaku.mcp.guardrails_doc import GUARDRAILS

    pol = _engine().policy
    return {"guardrails": GUARDRAILS, "policy": pol,
            "catalogue": {k: {"tier": v["tier"], "args": list(v["args"]), "optional": list(v["optional"])}
                          for k, v in CATALOGUE.items()}}


async def health() -> dict:
    return {"ok": True, "ts": time.time()}

