"""State machine, approvals and the executor's commit discipline, against a simulated Junos box."""

from __future__ import annotations

import time

import pytest

from shimpaku.actions import executor as X
from shimpaku.actions import policy as P
from shimpaku.actions.engine import ApprovalRefused, Caller, Engine
from shimpaku.actions.state import ActionStore, StateError


class Box:
    """Just enough Junos: admin state, a pending `commit confirmed`, a commit log."""

    def __init__(self):
        self.disabled = False
        self.candidate_disabled = False
        self.pending = False
        self.commits: list[dict] = []
        self.health = {"ospf_full": 2, "bgp_established": 1, "bfd_up": 2}


class FakeDev:
    def __init__(self, box):
        self.box = box
        self.connected = True

    def close(self):
        self.connected = False


class FakeConfig:
    def __init__(self, dev, mode=None):
        self.box = dev.box

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.box.candidate_disabled = self.box.disabled

    def load(self, text, format=None):
        self.box.candidate_disabled = text.startswith("set")

    def commit_check(self):
        pass

    def commit(self, comment=None, confirm=None):
        self.box.disabled = self.box.candidate_disabled
        self.box.pending = bool(confirm)
        self.box.commits.append({"comment": comment, "confirm": confirm})

    def rollback(self, rb_id=1):
        pass


@pytest.fixture
def box(monkeypatch, tmp_path):
    b = Box()
    import jnpr.junos.utils.config as cfgmod
    monkeypatch.setattr(cfgmod, "Config", FakeConfig)
    monkeypatch.setattr(X.lock, "pending_confirm", lambda dev: dev.box.pending)
    monkeypatch.setattr(X, "_admin", lambda dev, ifn: "down" if dev.box.disabled else "up")
    monkeypatch.setattr(X, "_newest_commit", lambda dev: {"time": "now", "log": (dev.box.commits or [{}])[-1].get("comment")})
    monkeypatch.setattr(X.devices, "health_snapshot", lambda dev: dict(dev.box.health))
    monkeypatch.setattr(X.devices, "port_facts", lambda dev, ifn: {"live_role": "unused", "interface_range_lines": [],
                                                                    "lldp_neighbor": None})
    return b


def make_engine(tmp_path, box, **pol_over):
    store = ActionStore(tmp_path / "state.db")
    pol = P.load_policy(None)
    pol.update({"validation_window_s": {"bounce_port": 0, "disable_port": 0}, "validation_interval_s": 0})
    pol.update(pol_over)

    def facts_fn(kind, args):
        return P.Facts(known_devices={"spine2", "leaf2", "spine1"}, topology_role="unused",
                       port={"live_role": "unused", "interface_range_lines": [], "lldp_neighbor": None,
                             "config_own": "", "config_inherited": ""},
                       pending_confirm=box.pending, open_actions_device=store.count_open(args.get("device")),
                       open_actions_total=store.count_open(),
                       bounces_24h=store.count_bounces(args.get("device", ""), args.get("ifname", ""), time.time() - 86400))

    ex = X.Executor(store, pol, opener=lambda name: FakeDev(box), lock_db=tmp_path / "locks.db",
                    sleep=lambda s: None)
    return Engine(store=store, policy=pol, facts_fn=facts_fn, executor=ex)


AGENT = Caller(kind="mcp", name="claude-code", transport="stdio")
HUMAN = Caller(kind="dashboard", name="rohan")


def test_full_lifecycle_to_verified(tmp_path, box):
    e = make_engine(tmp_path, box)
    a = e.propose("bounce_port", {"device": "spine2", "ifname": "ge-0/0/9"}, "lab test", AGENT)
    assert a["status"] == "awaiting_approval"
    e.tick()  # nothing runs without approval
    assert e.get(a["id"])["status"] == "awaiting_approval" and box.commits == []
    e.approve(a["id"], HUMAN)
    e.tick()   # executes, then the zero-length window validates on the same pass
    got = e.get(a["id"])
    assert got["status"] == "verified", got["timeline"]
    path = ["open"]
    for t in got["timeline"]:
        if t["to"] != path[-1]:
            path.append(t["to"])
    assert path == ["open", "awaiting_approval", "approved", "running", "validating", "verified"]
    # commit discipline: leg1 confirmed, leg2 plain (it is the confirm), comments carry the id
    assert [c["confirm"] for c in box.commits] == [2, None]
    assert all(c["comment"].startswith(f"shimpaku {a['id']}") for c in box.commits)
    assert not box.pending and not box.disabled


def test_agent_cannot_approve(tmp_path, box):
    e = make_engine(tmp_path, box)
    a = e.propose("bounce_port", {"device": "spine2", "ifname": "ge-0/0/9"}, "x reason", AGENT)
    for c in (AGENT, Caller(kind="assistant", name="hey"), {"kind": "mcp", "name": "dashboard"},
              Caller(kind="test", name="dashboard")):
        with pytest.raises(ApprovalRefused):
            e.approve(a["id"], c)
    assert e.get(a["id"])["status"] == "awaiting_approval"
    assert [r["decision"] for r in e.store.approvals(a["id"])] == ["refused"] * 4


def test_blocked_cannot_be_approved(tmp_path, box):
    e = make_engine(tmp_path, box)
    a = e.propose("bounce_port", {"device": "spine2", "ifname": "ge-0/0/*"}, "x", AGENT)
    assert a["status"] == "blocked"
    with pytest.raises(StateError):
        e.approve(a["id"], HUMAN)


def test_self_drive_t1(tmp_path, box):
    e = make_engine(tmp_path, box, self_drive={"bounce_port": True})
    a = e.propose("bounce_port", {"device": "spine2", "ifname": "ge-0/0/9"}, "x reason", AGENT)
    assert a["status"] == "automatic"
    e.tick()
    assert e.get(a["id"])["status"] == "verified"


def test_second_proposal_same_device_blocked_while_in_flight(tmp_path, box):
    e = make_engine(tmp_path, box)
    e.propose("bounce_port", {"device": "spine2", "ifname": "ge-0/0/9"}, "x reason", AGENT)
    b = e.propose("bounce_port", {"device": "spine2", "ifname": "ge-0/0/9"}, "x reason", AGENT)
    assert b["status"] == "blocked" and any("G5" in r for r in b["policy"]["reasons"])


def test_pending_confirm_refuses_execution(tmp_path, box):
    e = make_engine(tmp_path, box)
    a = e.propose("bounce_port", {"device": "spine2", "ifname": "ge-0/0/9"}, "x reason", AGENT)
    e.approve(a["id"], HUMAN)
    box.pending = True   # someone else committed confirmed in between
    e.tick()
    got = e.get(a["id"])
    assert got["status"] == "failed" and "pending" in got["error"] and box.commits == []


def test_failed_health_check_does_not_confirm(tmp_path, box, monkeypatch):
    e = make_engine(tmp_path, box)
    a = e.propose("bounce_port", {"device": "spine2", "ifname": "ge-0/0/9"}, "x reason", AGENT)
    e.approve(a["id"], HUMAN)
    calls = {"n": 0}

    def health(dev):
        calls["n"] += 1
        return {"ospf_full": 2 if calls["n"] == 1 else 1, "bgp_established": 1, "bfd_up": 2}
    monkeypatch.setattr(X.devices, "health_snapshot", health)
    e.tick()
    got = e.get(a["id"])
    assert got["status"] == "failed"
    assert [c["confirm"] for c in box.commits] == [2]  # only leg1; Junos timer will undo it
    assert box.pending


def test_recurrence_during_validation(tmp_path, box, monkeypatch):
    e = make_engine(tmp_path, box, validation_window_s={"bounce_port": 3600})
    a = e.propose("bounce_port", {"device": "spine2", "ifname": "ge-0/0/9"}, "x reason", AGENT)
    e.approve(a["id"], HUMAN)
    e.tick()
    assert e.get(a["id"])["status"] == "validating"
    box.disabled = True   # symptom back
    e.tick()
    assert e.get(a["id"])["status"] == "recurred"


def test_validation_timer_survives_restart(tmp_path, box):
    e = make_engine(tmp_path, box, validation_window_s={"bounce_port": 3600})
    a = e.propose("bounce_port", {"device": "spine2", "ifname": "ge-0/0/9"}, "x reason", AGENT)
    e.approve(a["id"], HUMAN)
    e.tick()
    row = e.store.get(a["id"])
    assert row["status"] == "validating" and row["next_check"] is not None
    e2 = make_engine(tmp_path, box, validation_window_s={"bounce_port": 3600})  # "restart"
    e2.store.transition(a["id"], "validating", "force due", next_check=0,
                        validation={**row["validation"], "ends": 0})
    e2.tick()
    assert e2.get(a["id"])["status"] == "verified"


def test_running_action_at_startup_is_recovered_not_redriven(tmp_path, box):
    e = make_engine(tmp_path, box)
    a = e.propose("bounce_port", {"device": "spine2", "ifname": "ge-0/0/9"}, "x reason", AGENT)
    e.approve(a["id"], HUMAN)
    e.store.transition(a["id"], "running", "simulated crash after leg1")
    box.pending = True
    e2 = make_engine(tmp_path, box)
    e2.tick()
    assert e2.get(a["id"])["status"] == "running"  # timer still pending: just observe
    box.pending, box.disabled = False, False         # Junos rolled back
    e2.tick()
    got = e2.get(a["id"])
    assert got["status"] == "failed" and "rolled back by itself" in got["timeline"][-1]["note"]
    assert box.commits == []


def test_bounce_cap_counts_executed_bounces(tmp_path, box):
    e = make_engine(tmp_path, box)
    for _ in range(3):
        a = e.propose("bounce_port", {"device": "spine2", "ifname": "ge-0/0/9"}, "x reason", AGENT)
        e.approve(a["id"], HUMAN)
        e.tick()
    a = e.propose("bounce_port", {"device": "spine2", "ifname": "ge-0/0/9"}, "x reason", AGENT)
    assert a["status"] == "blocked" and any(r.startswith("G2") for r in a["policy"]["reasons"])


def test_cancel_rules(tmp_path, box):
    e = make_engine(tmp_path, box)
    a = e.propose("bounce_port", {"device": "spine2", "ifname": "ge-0/0/9"}, "x reason", AGENT)
    with pytest.raises(ApprovalRefused):
        e.cancel(a["id"], Caller(kind="mcp", name="someone-else"))
    assert e.cancel(a["id"], AGENT)["status"] == "cancelled"


def test_add_vlan_validates_the_vlan_itself_not_device_health(tmp_path, box, monkeypatch):
    """Found live 2026-10-24: validation checked only device health, so a VLAN removed again
    during the window would still have ended 'verified'. It must watch the symptom."""
    state = {"vlan": False}
    orig = FakeConfig.commit

    def commit(self, comment=None, confirm=None):
        orig(self, comment, confirm)
        state["vlan"] = True

    monkeypatch.setattr(FakeConfig, "commit", commit)
    member = "set interfaces ge-0/0/9 unit 0 family ethernet-switching vlan members shimpaku-demo-999"
    monkeypatch.setattr(X.devices, "port_facts", lambda dev, ifn: {
        "live_role": "trunk", "interface_range_lines": [], "lldp_neighbor": None,
        "config_own": member if state["vlan"] else ""})
    e = make_engine(tmp_path, box, validation_window_s={"add_vlan_to_trunk": 3600})
    e.facts_fn = lambda kind, args: P.Facts(
        known_devices={"spine2"}, topology_role="unused", pending_confirm=False, vlan_seen_elsewhere=True,
        port={"live_role": "trunk", "interface_range_lines": [], "lldp_neighbor": None,
              "config_own": "", "config_inherited": ""})
    a = e.propose("add_vlan_to_trunk", {"device": "spine2", "ifname": "ge-0/0/9", "vlan": 999}, "restore", AGENT)
    assert a["status"] == "awaiting_approval", a["policy"]["reasons"]
    e.approve(a["id"], HUMAN)
    e.tick()
    assert e.get(a["id"])["status"] == "validating"
    state["vlan"] = False          # VLAN removed again; device health is untouched
    e.tick()
    assert e.get(a["id"])["status"] == "recurred"
