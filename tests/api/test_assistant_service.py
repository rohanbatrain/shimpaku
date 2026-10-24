"""Ask Shim in the daemon: events map to the panel contract, and Shim acts with the asker's role."""

import asyncio

from shimpaku import service
from shimpaku.assistant import loop
from shimpaku.mcp import server as mcp_mod


def _run(gen):
    async def collect():
        return [e async for e in gen]
    return asyncio.run(collect())


def test_unwired_daemon_reports_an_error_not_a_crash(monkeypatch):
    monkeypatch.setattr(service, "_mcp_server", None)
    assert _run(service.assistant_ask("hi", "operator"))[0]["type"] == "error"


def test_events_map_to_panel_contract_and_caller_is_the_askers(monkeypatch):
    seen = {}

    class FakeAssistant:
        def __init__(self, target, llm_factory=None):
            seen["target"], seen["factory"] = target, llm_factory

        async def ask(self, q):
            seen["caller"] = mcp_mod._caller.get(None)
            yield {"type": "route", "model": "m1"}
            yield {"type": "tool_call", "id": "c1", "name": "sle_get_summary", "args": {}}
            yield {"type": "tool_result", "id": "c1", "name": "sle_get_summary", "ok": True}
            yield {"type": "answer", "text": "all good"}
            yield {"type": "done", "steps": 1}

    monkeypatch.setattr(service, "_mcp_server", object())
    monkeypatch.setattr(loop, "Assistant", FakeAssistant)
    evs = _run(service.assistant_ask("q", "tester:inv_1", llm_key="sk-lease", role="tester", lease_id="lse_1"))
    assert [e["type"] for e in evs] == ["meta", "tool", "token", "done"]
    assert evs[1]["name"] == "sle_get_summary" and evs[1]["ok"] is True
    c = seen["caller"]
    assert (c.role, c.lease_id, c.transport, c.name) == ("tester", "lse_1", "inproc", "shim:tester:inv_1")
    assert seen["factory"] is not None  # the lease key, never the operator's
    assert mcp_mod._caller.get(None) is None  # reset afterwards


def test_operator_assistant_is_an_agent_not_an_operator(monkeypatch):
    seen = {}

    class FakeAssistant:
        def __init__(self, target, llm_factory=None):
            pass

        async def ask(self, q):
            seen["caller"] = mcp_mod._caller.get(None)
            yield {"type": "done"}

    monkeypatch.setattr(service, "_mcp_server", object())
    monkeypatch.setattr(loop, "Assistant", FakeAssistant)
    _run(service.assistant_ask("q", "operator", role="operator"))
    assert seen["caller"].role == "agent"  # Shim can propose for the operator, never approve
