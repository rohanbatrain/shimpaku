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


# ---------------------------------------------------------------------------- seed / break
# Fault injection is a Junos write, so it follows the executor's rules: lease the device
# (`lock.device_lock`, which also refuses while a `commit confirmed` is pending), exclusive
# candidate, comment naming the operation. Callers: `service.demo_seed` / `service.demo_break`
# (audited, role-checked); `demo_cli.py` calls those, never these directly.

SEED_LINES: dict[str, list[str]] = {
    DEVICE: [
        f"set vlans {vlan_name(KEEP_VLAN)} vlan-id {KEEP_VLAN}",
        f"set vlans {vlan_name(VLAN)} vlan-id {VLAN}",
        f"set interfaces {IFNAME} unit 0 family ethernet-switching interface-mode trunk",
        f"set interfaces {IFNAME} unit 0 family ethernet-switching vlan members {vlan_name(KEEP_VLAN)}",
        f"set interfaces {IFNAME} unit 0 family ethernet-switching vlan members {vlan_name(VLAN)}",
    ],
    CORR_DEVICE: [f"set vlans {vlan_name(VLAN)} vlan-id {VLAN}"],
}
BREAK_LINE = f"delete interfaces {IFNAME} unit 0 family ethernet-switching vlan members {vlan_name(VLAN)}"


def _write(dev_name: str, holder: str, why: str, load, *, opener, lock_db, confirm: int | None,
           comment: str | None = None) -> dict:
    """One locked, exclusive write. `load(cu)` loads the candidate. Returns {device, changed, diff}."""
    from jnpr.junos.utils.config import Config

    from shimpaku import lock

    dev = opener(dev_name)
    try:
        with lock.device_lock(dev_name, holder, why, dev=dev, wait=120, db=lock_db):
            with Config(dev, mode="exclusive") as cu:
                load(cu)
                diff = cu.diff()
                if not diff:
                    return {"device": dev_name, "changed": False, "diff": None}
                if confirm:
                    cu.commit(confirm=confirm, comment=f"shimpaku demo: {why}")
                    cu.commit(comment=f"shimpaku demo: {why} (confirm)")  # confirms the first
                else:
                    cu.commit(comment=comment or f"shimpaku demo: {why}")
        return {"device": dev_name, "changed": True, "diff": diff}
    finally:
        dev.close()


def apply_seed(holder: str, *, opener, lock_db) -> list[dict]:
    """Create the fixture (idempotent): the trunk with 998 + 999 on sw1, VLAN 999 on leaf2."""
    out = [_write(DEVICE, holder, "seed missing-vlan fixture",
                  lambda cu: cu.load("\n".join(SEED_LINES[DEVICE]), format="set", merge=True),
                  opener=opener, lock_db=lock_db, confirm=3)]
    out.append(_write(CORR_DEVICE, holder, "seed correlation vlan",
                      lambda cu: cu.load("\n".join(SEED_LINES[CORR_DEVICE]), format="set", merge=True),
                      opener=opener, lock_db=lock_db, confirm=3))
    return out


def apply_break(holder: str, *, opener, lock_db) -> list[dict]:
    """Inject the fault: VLAN 999 removed from the trunk (998 keeps the trunk valid)."""
    return [_write(DEVICE, holder, "inject missing vlan",
                   lambda cu: cu.load(BREAK_LINE, format="set", ignore_warning=True),
                   opener=opener, lock_db=lock_db, confirm=None,
                   comment=f"demo fault: vlan {VLAN} missing from trunk")]
