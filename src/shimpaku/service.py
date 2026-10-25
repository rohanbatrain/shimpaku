"""The one service API every surface calls (CONTRACT "Service layer"; owner B-mcp).

Writes that do not come through MCP (REST propose/approve/reject/cancel, CLI and system demo
operations) are written to the hash-chained audit log here; MCP tool calls are audited by
`mcp.server.AuditMiddleware`, so they are not written twice.

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
import ipaddress
import inspect
import json
import logging
import re
import time
from typing import Any, Callable, Literal

from shimpaku import devices

log = logging.getLogger("shimpaku.service")

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


async def probe_heatmap(site: str = "campus", window_min: int = 60) -> dict:
    """Stored RPM results as a probe-path × time grid (loss, RTT, jitter, n, SLE-bad minutes).
    Target addresses are never returned. Provider: B-analytics (``analytics/heatmap.py``)."""
    return await _call_provider("probe_heatmap", site=site, window_min=int(window_min))


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


async def device_get_health(device: str | None = None, window_min: int = 60) -> dict:
    """Stored health per device (no device call): latest CPU / memory samples and the device-minute
    verdicts of the last `window_min` minutes. Provider: the daemon's store (`providers.py`)."""
    return await _call_provider("device_get_health", device=device, window_min=int(window_min))


async def iface_get_history(device: str, window_min: int = 60) -> dict:
    """Per-interface traffic rates over time, computed from the collector's stored gNMI counters
    (`if.in_octets` / `if.out_octets` / packets). Provider: the daemon's store (`providers.py`)."""
    return await _call_provider("iface_get_history", device=device, window_min=int(window_min))


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


async def audit_chain(after: int | None = None, before: int | None = None, limit: int = 20) -> dict:
    """Raw audit rows (as stored, so their hashes recompute) with after < seq < before, newest
    first. For the chain visualiser; callers must not pass rows on without reducing them."""
    if _audit is None:
        return {"available": False, "reason": "audit log not initialised"}

    def q() -> dict:
        db = _audit.state
        with db._lock:
            con = db.connect()._con
            rows = [dict(r) for r in con.execute(
                "SELECT * FROM audit WHERE seq > ? AND seq < ? ORDER BY seq DESC LIMIT ?",
                (after or 0, before or 2**62, max(1, min(int(limit), 200))))]
            total = con.execute("SELECT count(*) FROM audit").fetchone()[0]
        return {"rows": rows, "total": total}
    return await asyncio.to_thread(q)


async def bench_results() -> dict:
    """The benchmark scorer's published output only (never the catalogue or ground truth)."""
    return await _call_provider("bench_results")


# ===================================================================== actions

def _engine():
    from shimpaku.actions.engine import engine
    return engine()


def _caller(c: Any):
    from shimpaku.actions.engine import Caller
    return Caller.coerce(c)


def _audited_by_mcp(caller) -> bool:
    """MCP tool calls are already audited, one row each, by `mcp.server.AuditMiddleware`."""
    return caller.kind == "mcp" and caller.transport in ("http", "stdio")


def _audit_row(caller, method: str, *, args: Any = None, action: dict | None = None,
               action_id: str | None = None, status: str = "ok", reasons: list[str] | None = None) -> None:
    """One hash-chained row for a write that did not come through MCP (REST, CLI, system). The
    audit log is the daemon's (`set_audit_log`); outside the daemon nothing is written here."""
    if _audit is None or _audited_by_mcp(caller):
        return
    pol = (action or {}).get("policy") or {}
    decision = ({k: pol.get(k) for k in ("allowed", "status", "reasons", "checked")} if pol
                else ({"allowed": False, "reasons": reasons} if reasons else None))
    source = caller.transport if caller.transport in ("rest", "cli") else "internal"
    _audit.append(caller=caller.as_dict(), client_info=caller.client, transport=caller.transport,
                  method=method, tool=method, args=args, tier=(action or {}).get("tier"),
                  policy_decision=decision, action_id=(action or {}).get("id") or action_id,
                  incident_id=(action or {}).get("incident_id"), result_status=status[:40], source=source)


def _status_of(out: dict) -> str:
    if isinstance(out.get("error"), dict):
        return f"error:{out['error'].get('code')}"
    return str(out.get("status") or "ok")


async def action_propose(kind: str, args: dict, reason: str, caller: Any) -> dict:
    """Propose an action. Policy (G0–G9) runs in code now; the result is the stored action with
    `status` = `blocked` (with `policy.reasons`), `awaiting_approval`, or `automatic` (T1 with
    self-driving on). Nothing executes from here — the executor picks up approved work.
    `caller` is a `shimpaku.actions.engine.Caller` (or dict) built by the transport."""
    c = _caller(caller)
    try:
        out = await asyncio.to_thread(_engine().propose, kind, args, reason, c)
    except Exception as e:  # noqa: BLE001
        out = _err("propose_failed", f"{type(e).__name__}: {e}")
    _audit_row(c, "action_propose", args={"kind": kind, "args": args, "reason": reason}, action=out,
               status=_status_of(out))
    return out


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


async def _decide(method: str, fn_name: str, action_id: str, caller: Any, *, raise_refused: bool) -> dict:
    """approve / reject / cancel: run the engine's rule, and audit the outcome, refusals included."""
    from shimpaku.actions.engine import ApprovalRefused
    from shimpaku.actions.state import StateError

    c = _caller(caller)
    try:
        out = await asyncio.to_thread(getattr(_engine(), fn_name), action_id, c)
    except ApprovalRefused as e:
        _audit_row(c, method, args={"action_id": action_id}, action_id=action_id, status="refused",
                   reasons=[str(e)])
        if raise_refused:
            raise
        return _err("forbidden", str(e))
    except StateError as e:
        out = _err("bad_state", str(e))
    except KeyError:
        out = _err("not_found", f"no action {action_id!r}")
    _audit_row(c, method, args={"action_id": action_id}, action=out if "id" in out else None,
               action_id=action_id, status=_status_of(out))
    return out


async def action_approve(action_id: str, user: Any) -> dict:
    """Approve an `awaiting_approval` action. Dashboard session only: `user` must be a Caller
    with kind="dashboard" (the API builds it from the session); a tester may approve only the
    fixture action of their own lease. Raises PermissionError otherwise; returns `{"error": ...}`
    for an unknown action or wrong state. Every outcome is an audit row."""
    return await _decide("action_approve", "approve", action_id, user, raise_refused=True)


async def action_reject(action_id: str, user: Any) -> dict:
    """Reject an `awaiting_approval` action (dashboard session only; testers: own lease's fixture)."""
    out = await _decide("action_reject", "reject", action_id, user, raise_refused=True)
    err = out.get("error")
    if isinstance(err, dict) and err.get("code") == "not_found":  # unchanged contract: 409, not 404
        return _err("bad_state", err["message"])
    return out


async def action_cancel(action_id: str, caller: Any) -> dict:
    """Cancel an action that has not started running (proposer or operator; testers: own lease)."""
    return await _decide("action_cancel", "cancel", action_id, caller, raise_refused=False)


# ===================================================================== demo fixture (fault lab)

DEMO_ROLES = ("operator", "tester", "system")


async def _demo_op(op: str, caller: Any, *, opener=None, lock_db=None) -> dict:
    """`demo_seed` / `demo_break`: a Junos write on the fixture devices only, under the device
    lock (refused while a `commit confirmed` is pending), one audit row per call with the
    caller's role and lease. Operators, testers (with a lease) and the system may run it."""
    from shimpaku.actions import fixture
    from shimpaku.config import settings

    c = _caller(caller)
    method = f"demo_{op}"
    refusal = None
    if c.role not in DEMO_ROLES:
        refusal = f"{method} refused: role {c.role!r} may not change the demo fixture"
    elif c.role == "tester" and not c.lease_id:
        refusal = f"{method} refused: tester has no lease"
    if refusal:
        _audit_row(c, method, args={"devices": _demo_devices(op)}, status="refused", reasons=[refusal])
        return _err("forbidden", refusal)
    holder = f"shimpaku-demo:{c.role}:{c.lease_id or c.name}"
    fn = fixture.apply_seed if op == "seed" else fixture.apply_break
    try:
        writes = await asyncio.to_thread(fn, holder, opener=opener or devices.open_device,
                                         lock_db=lock_db or settings.lock_db)
    except Exception as e:  # noqa: BLE001  (DeviceBusy, PendingConfirm, NETCONF errors)
        from shimpaku import lock
        code = "busy" if isinstance(e, (lock.DeviceBusy, lock.PendingConfirm)) else "device_error"
        # testers read this reply and every reader sees the audit row: no management addresses
        log.warning("%s failed: %s: %s", method, type(e).__name__, e)
        out = _err(code, scrub_hosts(f"{type(e).__name__}: {e}"))
        _audit_row(c, method, args={"devices": _demo_devices(op)}, status=f"error:{type(e).__name__}",
                   reasons=[scrub_hosts(str(e))[:300]])
        return out
    changed = any(w["changed"] for w in writes)
    out = {"op": op, "changed": changed, "writes": writes}
    _audit_row(c, method, args={"devices": _demo_devices(op),
                                "diff": {w["device"]: w["diff"] for w in writes if w["diff"]}},
               status="changed" if changed else "no_change")
    return out


_IPV4 = re.compile(r"(?<![\w.])\d{1,3}(?:\.\d{1,3}){3}(?![\w.])")
_IPV6 = re.compile(r"(?<![\w:])[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7}(?![\w:])")


def scrub_hosts(text: str) -> str:
    """Replace IPv4/IPv6 addresses (management addresses in device errors) with `<host>`."""
    def sub(m: re.Match) -> str:
        try:
            ipaddress.ip_address(m.group(0))
        except ValueError:
            return m.group(0)
        return "<host>"
    return _IPV6.sub(sub, _IPV4.sub(sub, text))


def _demo_devices(op: str) -> list[str]:
    from shimpaku.actions import fixture
    return [fixture.DEVICE, fixture.CORR_DEVICE] if op == "seed" else [fixture.DEVICE]


async def demo_seed(caller: Any, **kw: Any) -> dict:
    """Create the Missing-VLAN fixture (idempotent). Audited; takes the device lock."""
    return await _demo_op("seed", caller, **kw)


async def demo_break(caller: Any, **kw: Any) -> dict:
    """Inject the fault: VLAN 999 removed from the sw1 ge-0/0/5 trunk. Audited; takes the lock."""
    return await _demo_op("break", caller, **kw)


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



# ===================================================================== assistant (dashboard ⌘K)

_mcp_server = None


def set_mcp_server(server) -> None:
    """The daemon's MCP server; Ask Shim talks to it in memory, so it uses the same tools,
    guardrails and audit as any external agent, without a network hop or a bearer token."""
    global _mcp_server
    _mcp_server = server


def _args_summary(args: Any) -> str:
    """A tool call's arguments for the reasoning trace: redacted, scrubbed of addresses, short."""
    from shimpaku.mcp.audit import redact

    if not isinstance(args, dict) or not args:
        return ""
    s = ", ".join(f"{k}={v if isinstance(v, (str, int, float, bool)) or v is None else json.dumps(v, default=str)}"
                  for k, v in redact(args).items())
    return scrub_hosts(s)[:140]


async def assistant_ask(question: str, principal: str, context: dict | None = None, *,
                        llm_key: str | None = None, role: str | None = None,
                        lease_id: str | None = None):
    """Run Shim for a dashboard user and translate its events into the panel's contract
    (meta, tool, token, error, done). Shim acts as `shim:<principal>` with the asker's role and
    lease, so a tester's assistant is held to the tester gate; it can propose, never approve.
    A `visitor` asker (e.g. a `read` token) gets a Shim that cannot propose either."""
    import time as _t

    from shimpaku.actions.engine import Caller
    from shimpaku.assistant.llm import LLM
    from shimpaku.assistant.loop import Assistant
    from shimpaku.mcp import server as mcp_mod

    if _mcp_server is None:
        yield {"type": "error", "message": "the assistant is not wired into this daemon"}
        return
    caller = Caller(kind="assistant", name=f"shim:{principal}", transport="inproc",
                    role=role if role in ("tester", "visitor") else "agent", lease_id=lease_id)
    token = mcp_mod._caller.set(caller)
    factory = (lambda name: LLM(name, key=llm_key)) if llm_key else None
    started: dict[str, float] = {}
    arg_s: dict[str, str] = {}
    streamed = False
    try:
        bot = Assistant(_mcp_server, llm_factory=factory)
        async for ev in bot.ask(question):
            t = ev.get("type")
            if t == "route":
                yield {"type": "meta", "model": ev.get("model", "?")}
            elif t == "tool_call":
                started[ev["id"]] = _t.perf_counter()
                arg_s[ev["id"]] = _args_summary(ev.get("args"))
            elif t == "tool_result":
                ms = int((_t.perf_counter() - started.pop(ev["id"], _t.perf_counter())) * 1000)
                yield {"type": "tool", "name": ev.get("name"), "call": ev.get("name"), "ms": ms,
                       "ok": bool(ev.get("ok")), "args": arg_s.pop(ev["id"], ""),
                       "ids": [str(x)[:60] for x in (ev.get("ids") or [])][:8]}
            elif t == "token":
                streamed = True
                yield ev
            elif t == "answer" and not streamed and ev.get("text"):
                yield {"type": "token", "text": ev["text"]}
            elif t in ("error", "done"):
                yield {"type": t, **({"message": ev.get("message", "")} if t == "error" else {})}
    finally:
        mcp_mod._caller.reset(token)
