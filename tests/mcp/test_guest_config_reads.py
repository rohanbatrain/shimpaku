"""Guests read config only on the fixture's devices, with secret lines redacted (DEPLOY-DESIGN D4)."""

from shimpaku.actions.engine import Caller
from shimpaku.mcp import server as m


def _as(role):
    return m._caller.set(Caller(kind="mcp", name="x", transport="inproc", role=role, lease_id="l1"))


def test_tester_refused_off_fixture_devices():
    t = _as("tester")
    try:
        assert m._guest_device_refusal("spine1")["error"]["code"] == "forbidden"
        assert m._guest_device_refusal("sw1") is None and m._guest_device_refusal("leaf2") is None
    finally:
        m._caller.reset(t)


def test_operator_agent_unrestricted():
    t = _as("agent")
    try:
        assert m._guest_device_refusal("spine1") is None and not m._guest()
    finally:
        m._caller.reset(t)


def test_secret_lines_redacted():
    diff = ('[edit system login user ops authentication]\n+ encrypted-password "$6$abc"; ## SECRET-DATA\n'
            '[edit snmp]\n+ community public;\n+ vlan members shimpaku-demo-999;')
    out = m._redact_secrets({"diff": diff, "rows": [diff]})
    for text in (out["diff"], out["rows"][0]):
        assert "$6$abc" not in text and "community public" not in text
        assert "vlan members shimpaku-demo-999" in text
