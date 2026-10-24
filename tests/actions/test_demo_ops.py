"""Build step 3 (docs/DEPLOY-DESIGN.md): `demo seed` / `demo break` are audited service operations
under the device lock, and tester actions validate for 180 s (policy-configurable)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_engine import box, make_engine  # noqa: E402,F401

from shimpaku import lock, service  # noqa: E402
from shimpaku.actions import executor as X  # noqa: E402
from shimpaku.actions import fixture  # noqa: E402
from shimpaku.actions import policy as P  # noqa: E402
from shimpaku.actions.engine import Caller  # noqa: E402
from shimpaku.mcp.audit import AuditLog  # noqa: E402

CLI_OPERATOR = Caller(kind="cli", name="os:lab", transport="cli", role="operator")
TESTER = Caller(kind="dashboard", name="tester:p1", transport="rest", role="tester", lease_id="L1")


class Junos:
    """The fixture devices: config lines per device, a pending flag, the commits made."""

    def __init__(self):
        self.lines = {"sw1": set(fixture.SEED_LINES["sw1"]), "leaf2": set(fixture.SEED_LINES["leaf2"])}
        self.pending = False
        self.commits: list[tuple[str, str, int | None, list]] = []


class Dev:
    def __init__(self, j, name):
        self.j, self.name = j, name

    def close(self):
        pass


@pytest.fixture
def lab(monkeypatch, tmp_path):
    j = Junos()
    lock_db = tmp_path / "locks.db"

    class Config:
        def __init__(self, dev, mode=None):
            assert mode == "exclusive"
            self.dev, self.cand = dev, set(j.lines[dev.name])

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def load(self, text, format=None, merge=None, ignore_warning=None):
            for line in text.splitlines():
                if line.startswith("set "):
                    self.cand.add(line)
                elif line.startswith("delete "):
                    self.cand.discard("set " + line[len("delete "):])

        def diff(self):
            run = j.lines[self.dev.name]
            return "\n".join([f"+ {x}" for x in sorted(self.cand - run)] + [f"- {x}" for x in sorted(run - self.cand)]) or None

        def commit(self, comment=None, confirm=None):
            held = [h["holder"] for h in lock.holders(db=lock_db) if h["device"] == self.dev.name]
            j.commits.append((self.dev.name, comment, confirm, held))
            j.lines[self.dev.name] = set(self.cand)

    import jnpr.junos.utils.config as cfgmod
    monkeypatch.setattr(cfgmod, "Config", Config)
    monkeypatch.setattr(lock, "pending_confirm", lambda dev: j.pending)
    audit = AuditLog(tmp_path / "state.db", tmp_path / "audit.jsonl")
    service.set_audit_log(audit)
    kw = {"opener": lambda name: Dev(j, name), "lock_db": lock_db}
    yield j, audit, kw
    service.set_audit_log(None)


def member999():
    return f"set interfaces {fixture.IFNAME} unit 0 family ethernet-switching vlan members {fixture.vlan_name(999)}"


def test_break_and_seed_are_audited_and_locked(lab):
    j, audit, kw = lab
    out = asyncio.run(service.demo_break(CLI_OPERATOR, **kw))
    assert out["changed"] and member999() not in j.lines["sw1"]
    dev, comment, confirm, held = j.commits[-1]
    assert dev == "sw1" and comment == "demo fault: vlan 999 missing from trunk"
    assert held == ["shimpaku-demo:operator:os:lab"]  # written under the device lock
    assert not asyncio.run(service.demo_break(CLI_OPERATOR, **kw))["changed"]  # idempotent
    out = asyncio.run(service.demo_seed(TESTER, **kw))
    assert out["changed"] and member999() in j.lines["sw1"]
    assert all(c[3] == ["shimpaku-demo:tester:L1"] for c in j.commits[1:])
    rows = audit.query(limit=10)  # newest first
    assert [(r["tool"], r["status"]) for r in rows] == [
        ("demo_seed", "changed"), ("demo_break", "no_change"), ("demo_break", "changed")]
    assert rows[2]["kind"] == "cli:demo_break" and rows[2]["actor"]["role"] == "operator"
    assert rows[0]["kind"] == "rest:demo_seed" and rows[0]["actor"]["role"] == "tester" \
        and rows[0]["actor"]["lease_id"] == "L1"
    assert audit.verify()["ok"]


@pytest.mark.parametrize("caller", [
    Caller(kind="dashboard", name="t", role="tester", transport="rest"),      # tester without a lease
    Caller(kind="mcp", name="token:cc", transport="rest"),                    # an agent
    Caller(kind="dashboard", name="v", role="visitor", transport="rest"),
], ids=["tester-no-lease", "agent", "visitor"])
def test_demo_ops_refused_for_other_roles_and_audited(lab, caller):
    j, audit, kw = lab
    out = asyncio.run(service.demo_break(caller, **kw))
    assert out["error"]["code"] == "forbidden" and j.commits == []
    r = audit.query(tool="demo_break")[0]
    assert r["status"] == "refused" and r["actor"]["role"] == caller.role
    assert audit.verify()["ok"]


def test_demo_op_refused_while_a_confirm_is_pending(lab):
    j, audit, kw = lab
    j.pending = True
    out = asyncio.run(service.demo_break(TESTER, **kw))
    assert out["error"]["code"] == "busy" and j.commits == []
    assert audit.query(tool="demo_break")[0]["status"] == "error:PendingConfirm"


def test_cli_calls_the_same_service_operation(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from shimpaku import demo_cli
    seen = {}

    async def fake_break(caller, **kw):
        seen["caller"] = caller
        return {"op": "break", "changed": True, "writes": []}

    monkeypatch.setattr(service, "demo_break", fake_break)
    monkeypatch.setattr(type(demo_cli.settings), "require_device_password", lambda self: None)
    monkeypatch.setattr(service, "_audit", object())  # already wired; the CLI must not build another
    r = CliRunner().invoke(demo_cli.app, ["break"])
    assert r.exit_code == 0, r.output
    c = seen["caller"]
    assert (c.kind, c.transport, c.role) == ("cli", "cli", "operator")


# ---- per-action validation window ---------------------------------------------------------------

@pytest.fixture
def vlan_engine(tmp_path, box, monkeypatch):  # noqa: F811
    monkeypatch.setattr(X.devices, "port_facts", lambda dev, ifn: {
        "live_role": "trunk", "interface_range_lines": [], "lldp_neighbor": None, "config_own": member999()})

    def build(**pol):
        e = make_engine(tmp_path, box, validation_window_s={"add_vlan_to_trunk": 1800}, **pol)
        e.facts_fn = lambda kind, args: P.Facts(
            known_devices={"sw1"}, topology_role="unused", pending_confirm=False, vlan_seen_elsewhere=True,
            port={"live_role": "trunk", "interface_range_lines": [], "lldp_neighbor": None,
                  "config_own": "", "config_inherited": ""})
        return e
    return build


def _run_to_validating(e, caller):
    a = e.propose(fixture.KIND, dict(fixture.ARGS), "restore", caller)
    assert a["status"] == "awaiting_approval", a["policy"]["reasons"]
    e.approve(a["id"], TESTER if caller.role == "tester" else Caller(kind="dashboard", name="admin"))
    e.tick()
    got = e.store.get(a["id"])
    assert got["status"] == "validating", got["timeline"]
    return got["validation"]["ends"] - got["validation"]["starts"]


def test_tester_actions_validate_for_180_s(vlan_engine):
    assert round(_run_to_validating(vlan_engine(), TESTER)) == 180


def test_tester_window_is_policy_configurable(vlan_engine):
    assert round(_run_to_validating(vlan_engine(tester_validation_window_s=90), TESTER)) == 90


def test_operator_keeps_per_kind_window(vlan_engine):
    assert round(_run_to_validating(vlan_engine(), Caller(kind="mcp", name="cc", transport="stdio"))) == 1800
