"""The demo fixture: the one action a tester may propose, approve, reject or cancel.

`add_vlan_to_trunk` on `sw1 ge-0/0/5` (a throwaway trunk wired to nothing), VLAN 999 only. VLAN
998 stays on the trunk as a keepalive (Junos refuses a trunk with no members); VLAN 999 is also
defined on the correlation device `leaf2`, which guardrail G3 checks. `demo_cli.py`, the engine's
tester gate (`engine.Engine.propose`) and the demo service operations all read these constants;
nothing else spells them out.
"""

from __future__ import annotations

from typing import Any

KIND = "add_vlan_to_trunk"
DEVICE = "sw1"
IFNAME = "ge-0/0/5"
VLAN = 999            # removed by `demo break`, restored by the fixture action
KEEP_VLAN = 998       # keepalive member
CORR_DEVICE = "leaf2"  # carries VLAN 999 so G3 can corroborate it

ARGS: dict[str, Any] = {"device": DEVICE, "ifname": IFNAME, "vlan": VLAN}


def vlan_name(vlan: int) -> str:
    return f"shimpaku-demo-{vlan}"


def is_fixture(kind: Any, args: Any) -> bool:
    """Exactly the fixture: same kind, same device/port/VLAN (an int, not 999.0 or "999"), and no
    other argument except an optional string `incident_id`. Anything else is not the fixture."""
    if kind != KIND or not isinstance(args, dict):
        return False
    extra = set(args) - set(ARGS)
    if extra - {"incident_id"}:
        return False
    if "incident_id" in args and not isinstance(args["incident_id"], str):
        return False
    vlan = args.get("vlan")
    return (args.get("device") == DEVICE and args.get("ifname") == IFNAME
            and type(vlan) is int and vlan == VLAN)


def tester_policy(policy: dict) -> dict:
    """The policy a tester's proposal is evaluated under: the operator's policy with the write
    scope narrowed to the fixture port, so G0 refuses anything else even if the tester gate in the
    engine were bypassed. Tester actions never self-drive: the tester approves them."""
    p = dict(policy)
    p["write_scope_ports"] = {DEVICE: [IFNAME]}
    p["test_ports"] = {DEVICE: [IFNAME]}
    p["write_scope_devices"] = []
    p["write_scope_topologies"] = []
    p["demo_vlans"] = [VLAN]
    p["self_drive"] = {}
    return p
