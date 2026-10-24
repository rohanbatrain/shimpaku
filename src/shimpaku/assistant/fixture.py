"""In-process mock of shimpakud's MCP server for the eval and the tests.

Tool names, arguments and the action-result shape here follow the REAL server
(``src/shimpaku/mcp/server.py``, B-mcp, commit 364075a) and CONTRACT.md's MVP action catalogue —
not just R7's exploratory research. Concretely, vs. the R7 lab-book draft:

- An action result's id field is ``id`` (not ``action_id``); status is one of the real
  ``shimpaku.actions.state`` values: ``blocked | awaiting_approval | automatic | approved |
  running | validating | verified | recurred | failed | cancelled | rejected``.
- ``sle_get_summary`` takes a required ``site`` (``campus`` | ``fabric``); ``probe_run``'s
  ``kind`` defaults to ``"ping"``; ``action_propose_rerun_deploy`` takes ``topology``, not
  ``site``; every ``action_propose_*`` takes an optional ``incident_id``.
- CONTRACT.md's MVP action catalogue is only `bounce_port`, `rerun_deploy`, `disable_port`,
  `rollback` — no `add_vlan_to_trunk`. R7's S02 scenario (missing VLAN on a trunk) predates that
  decision; this fixture does not expose the tool, and we patch S02's acceptable answer to
  "diagnose, propose nothing" below, matching what a real agent can actually do today.

Still backed by R7's 14 mocked scenarios (``r7_scenarios``, vendored verbatim). Every call is
logged in R7's record format (tool names translated back) so the R7-derived scorer applies.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

from shimpaku.assistant import r7_scenarios as S

# contract tool name -> R7 mock name (no add_vlan_to_trunk: not in the MVP action catalogue)
TO_R7 = {
    "sle_get_summary": "get_sle_summary", "sle_get_blast_radius": "get_blast_radius", "probe_run": "run_probe",
    "iface_get_errors": "get_interface_errors", "port_get_config": "get_port_config", "topo_get_neighbors": "get_lldp",
    "routing_get_health": "get_routing_health", "config_get_commits": "get_recent_commits",
    "action_propose_bounce_port": "bounce_port", "action_propose_disable_port": "disable_port",
    "action_propose_rollback": "rollback_config",
}
# real shimpaku.actions.state vocabulary
NOT_EXECUTED = {"open", "proposed", "awaiting_approval", "blocked", "cancelled", "rejected"}
EXECUTED = {"automatic", "approved", "running", "validating", "verified", "recurred", "failed"}
RO = ToolAnnotations(read_only_hint=True)
T1 = ToolAnnotations(destructive_hint=False, idempotent_hint=True)
T2 = ToolAnnotations(destructive_hint=True)


def _site(node: str) -> str:
    return "campus" if node in ("sw1", "r1", "r2") else "fabric"


def _patched_scenarios() -> dict[str, dict[str, Any]]:
    """R7's scenarios, with S02 adjusted to the real MVP action catalogue (see module docstring)."""
    out = {s["id"]: s for s in S.SCENARIOS}
    s02 = copy.deepcopy(out["S02-missing-vlan"])
    s02["accept"] = [None]
    s02["kind"] = "diagnose-only"
    out["S02-missing-vlan"] = s02
    return out


SC_BY_ID = _patched_scenarios()
SCENARIOS = list(SC_BY_ID.values())


class MockLab:
    """One scenario's world. ``calls`` is the R7-format call log; ``server`` the MCP server."""

    def __init__(self, scenario: dict[str, Any]) -> None:
        self.sc = SC_BY_ID.get(scenario["id"], scenario)
        self.calls: list[dict[str, Any]] = []
        self.observed: list[str] = []
        self.proposals: dict[str, dict[str, Any]] = {}
        self.server = self._build()

    # ------------------------------------------------------------------ helpers
    def _new_id(self) -> str:
        return "act-" + self.sc["id"].split("-")[0].lower() + f"-{len(self.proposals) + 1}"

    def _r7_action(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """Run an R7 action tool through its guardrail, then reshape to the real action result."""
        result, meta = S.execute(tool, args, self.sc)
        meta["contract_tool"] = next((k for k, v in TO_R7.items() if v == tool), tool)
        meta["result_status"] = result.get("status") or ("error" if "error" in result else "ok")
        self.calls.append(meta)
        if "error" in result:
            return result
        aid = self._new_id()
        if result.get("status") == "BLOCKED_BY_GUARDRAIL":
            out = {"id": aid, "status": "blocked", "policy": {"allowed": False, "reasons": [result["reason"]]}}
        else:
            out = {"id": aid, "status": "awaiting_approval", "policy": {"allowed": True}}
        self.proposals[aid] = {**out, "kind": tool, "args": meta.get("args")}
        self.observed.append(str(out))
        return out

    def _r7(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        result, meta = S.execute(tool, args, self.sc)
        meta["contract_tool"] = next((k for k, v in TO_R7.items() if v == tool), tool)
        meta["result_status"] = result.get("status") or ("error" if "error" in result else "ok")
        self.calls.append(meta)
        self.observed.append(str(result))
        return result

    def _incidents(self) -> list[dict[str, Any]]:
        over = self.sc.get("over", {})
        if "get_sle_summary" not in over:
            return []
        sles = [s for s in over["get_sle_summary"]["*"]["sles"] if s.get("top_classifier")]
        return [{"incident_id": f"inc-{self.sc['id'].split('-')[0].lower()}", "status": "open",
                 "sle": s["sle"], "vlan": s["vlan"], "classifier": s["top_classifier"],
                 "opened": S.NOW} for s in sles[:1]]

    def _other(self, contract: str, args: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"tool": contract, "contract_tool": contract, "valid": "error" not in result,
                           "kind": "read", "args": args, "result_status": result.get("status", "ok")})
        self.observed.append(str(result))
        return result

    # ------------------------------------------------------------------ server
    def _build(self) -> MCPServer:  # noqa: C901 — one closure per tool, deliberately flat
        srv = MCPServer("shimpaku")
        lab = self

        @srv.tool(description="List devices (name, site, role, mgmt state). site: campus | fabric.", annotations=RO)
        def inventory_list(site: str | None = None) -> dict[str, Any]:
            devs = [{"device": n, "site": _site(n), "role": ("spine" if n.startswith("spine") else "leaf" if n.startswith("leaf")
                     else "access-switch" if n == "sw1" else "router"), "reachable": True} for n in S.NODES]
            if site:
                devs = [d for d in devs if d["site"] == site]
            return lab._other("inventory_list", {"site": site}, {"devices": devs})

        @srv.tool(description="Service-level expectations over the last window_min minutes for one site: per SLE/VLAN success %, goal % and top failure classifier.", annotations=RO)
        def sle_get_summary(site: str, window_min: int = 60) -> dict[str, Any]:
            return lab._r7("get_sle_summary", {"minutes": window_min})

        @srv.tool(description="Blast radius of an incident: features (interface, device, VLAN, client, server) ranked by mutual information with failing minutes.", annotations=RO)
        def sle_get_blast_radius(incident_id: str) -> dict[str, Any]:
            inc = next((i for i in lab._incidents() if i["incident_id"] == incident_id), None)
            if not inc:
                return lab._other("sle_get_blast_radius", {"incident_id": incident_id},
                                  {"error": f"unknown incident {incident_id!r}; use incident_list"})
            r = lab._r7("get_blast_radius", {"sle": inc["sle"]})
            return {"incident_id": incident_id, **r}

        @srv.tool(description="Run a synthetic client probe now against a target (VLAN id, e.g. '10'). kind: ping | dhcp | dns | http | arp.", annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True))
        def probe_run(target: str, kind: str = "ping") -> dict[str, Any]:
            m = re.search(r"\d+", str(target))
            return lab._r7("run_probe", {"vlan": int(m.group(0)) if m else target})

        @srv.tool(description="Interface state and counters (admin/oper, MTU, flaps, 10-min packet deltas, input/CRC errors, output drops). Omit ifname for every interface of the device.", annotations=RO)
        def iface_get_errors(device: str, ifname: str | None = None) -> dict[str, Any]:
            if ifname:
                return lab._r7("get_interface_errors", {"node": device, "ifname": ifname})
            d = str(device).strip().lower()
            if d not in S.PORTS:
                return lab._r7("get_interface_errors", {"node": device, "ifname": "ge-0/0/0"})
            return {"device": d, "interfaces": [lab._r7("get_interface_errors", {"node": d, "ifname": i}) for i in S.PORTS[d]]}

        @srv.tool(description="One port's effective config: access (one VLAN), trunk (VLAN list), fabric or routed, plus description.", annotations=RO)
        def port_get_config(device: str, ifname: str) -> dict[str, Any]:
            return lab._r7("get_port_config", {"node": device, "ifname": ifname})

        @srv.tool(description="LLDP neighbours: local port -> remote device and port.", annotations=RO)
        def topo_get_neighbors(device: str | None = None) -> dict[str, Any]:
            if device:
                return lab._r7("get_lldp", {"node": device})
            return {"devices": [lab._r7("get_lldp", {"node": n}) for n in S.NODES]}

        @srv.tool(description="OSPF neighbours, BGP peers and BFD sessions of a device.", annotations=RO)
        def routing_get_health(device: str | None = None) -> dict[str, Any]:
            if device:
                return lab._r7("get_routing_health", {"node": device})
            return {"devices": [lab._r7("get_routing_health", {"node": n}) for n in S.NODES]}

        @srv.tool(description="Newest configuration commits (time, user, comment, diff summary). rollback_id 0 is the current config.", annotations=RO)
        def config_get_commits(device: str, n: int = 10) -> dict[str, Any]:
            return lab._r7("get_recent_commits", {"node": device, "count": n})

        @srv.tool(description="What the last `rollback` commits changed on a device.", annotations=RO)
        def config_get_diff(device: str, rollback: int = 1) -> dict[str, Any]:
            r = lab._r7("get_recent_commits", {"node": device, "count": 50})
            if "error" in r:
                return r
            cur = next((c for c in r["commits"] if c["rollback_id"] == 0), None)
            return {"device": r["node"], "rollback": rollback, "diff": cur["diff"] if cur and rollback == 1 else "(see commits)"}

        @srv.tool(description="Incidents, newest first, optionally by status.", annotations=RO)
        def incident_list(status: str | None = None) -> dict[str, Any]:
            return lab._other("incident_list", {"status": status}, {"incidents": lab._incidents()})

        @srv.tool(description="One incident with evidence, classifier and linked actions.", annotations=RO)
        def incident_get(incident_id: str) -> dict[str, Any]:
            inc = next((i for i in lab._incidents() if i["incident_id"] == incident_id), None)
            return lab._other("incident_get", {"incident_id": incident_id}, inc or {"error": f"unknown incident {incident_id!r}"})

        @srv.tool(description="Audit rows (newest first): who called what, the policy decision and the result.", annotations=RO)
        def audit_query(tool: str | None = None, action_id: str | None = None, status: str | None = None, limit: int = 50) -> dict[str, Any]:
            rows = [{"tool": c.get("contract_tool", c["tool"]), "args": c.get("args"), "result_status": c.get("result_status")}
                   for c in lab.calls if (not tool or c.get("contract_tool") == tool)]
            return lab._other("audit_query", {"tool": tool, "action_id": action_id, "status": status}, {"audit": rows[-limit:]})

        # ------------------------------------------------------- T1 / T2 (propose only; MVP catalogue)
        @srv.tool(description="T1 PROPOSAL (does not execute): bounce ONE access port (disable, re-enable) to clear a stuck state. Returns an id; a human approves it on the dashboard. Trunk, fabric and routed ports are refused; capped at 3 bounces per port per 24h.", annotations=T1)
        def action_propose_bounce_port(device: str, ifname: str, reason: str, incident_id: str | None = None) -> dict[str, Any]:
            return lab._r7_action("bounce_port", {"node": device, "ifname": ifname, "reason": reason})

        @srv.tool(description="T1 PROPOSAL (does not execute): re-run the generator deploy to restore drift from the source of truth. Returns an id; a human approves it on the dashboard.", annotations=T1)
        def action_propose_rerun_deploy(topology: str, reason: str, incident_id: str | None = None) -> dict[str, Any]:
            aid = lab._new_id()
            lab.calls.append({"tool": "rerun_deploy", "contract_tool": "action_propose_rerun_deploy", "valid": True,
                              "kind": "action", "args": {"topology": topology}, "guardrail": "allowed", "result_status": "awaiting_approval"})
            out = {"id": aid, "status": "awaiting_approval", "policy": {"allowed": True}}
            lab.proposals[aid] = {**out, "kind": "rerun_deploy", "args": {"topology": topology}}
            return out

        @srv.tool(description="T2 PROPOSAL (does not execute; always human-approved): administratively disable ONE access port (rogue DHCP, loop). Returns an id.", annotations=T2)
        def action_propose_disable_port(device: str, ifname: str, reason: str, incident_id: str | None = None) -> dict[str, Any]:
            return lab._r7_action("disable_port", {"node": device, "ifname": ifname, "reason": reason})

        @srv.tool(description="T2 PROPOSAL (does not execute; always human-approved): roll ONE device back to rollback_id (1 = before the last commit) with commit confirmed. Returns an id.", annotations=T2)
        def action_propose_rollback(device: str, reason: str, rollback_id: int = 1, incident_id: str | None = None) -> dict[str, Any]:
            return lab._r7_action("rollback_config", {"node": device, "rollback_id": rollback_id, "reason": reason})

        # ------------------------------------------------------- control
        @srv.tool(description="Status, timeline and validation of one action. Poll this after proposing: blocked | awaiting_approval | automatic | approved | running | validating | verified | recurred | failed | cancelled | rejected.", annotations=RO)
        def action_get(action_id: str) -> dict[str, Any]:
            a = lab.proposals.get(action_id)
            return lab._other("action_get", {"action_id": action_id},
                              copy.deepcopy(a) if a else {"error": f"unknown action {action_id!r}"})

        @srv.tool(description="Actions, optionally by status.", annotations=RO)
        def action_list(status: str | None = None) -> dict[str, Any]:
            items = [a for a in lab.proposals.values() if not status or a["status"] == status]
            return lab._other("action_list", {"status": status}, {"actions": copy.deepcopy(items)})

        @srv.tool(description="Cancel an action you proposed that has not started running.")
        def action_cancel(action_id: str) -> dict[str, Any]:
            a = lab.proposals.get(action_id)
            if a:
                a["status"] = "cancelled"
            return lab._other("action_cancel", {"action_id": action_id}, copy.deepcopy(a) if a else {"error": "unknown action"})

        return srv
