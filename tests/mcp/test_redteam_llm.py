"""Self-test of the live-LLM red-team harness's own logic (scoring, reporting, the missing-key
guard), with a stubbed Assistant — no gateway key, no network, no lab. Proves the harness is
wired correctly *before* it is ever pointed at a real model (CONTRACT: operator-grade tests)."""

from __future__ import annotations

import argparse
import json

import pytest

from shimpaku.mcp import redteam_llm as rt

class FakeAssistant:
    """Stands in for shimpaku.assistant.loop.Assistant: same constructor shape, scripted events."""

    SCRIPT: dict[str, list[dict]] = {}  # set per-test

    def __init__(self, mcp_url, *, model="auto", max_steps=8, seed=None):
        self.mcp_url, self.model, self.seed = mcp_url, model, seed

    async def ask(self, prompt):
        for ev in FakeAssistant.SCRIPT.get(prompt, []):
            yield ev


def answer_event(text: str, actions: list[dict]) -> dict:
    return {"type": "answer", "text": text, "answer": {"actions": actions, "evidence_ids": [],
                                                        "tools_used": [], "corrected": False}}


@pytest.mark.asyncio
async def test_run_scenario_passes_when_nothing_executed():
    FakeAssistant.SCRIPT = {"p": [
        {"type": "route", "role": "diagnose", "model": "m", "reason": "auto"},
        {"type": "tool_call", "id": "1", "name": "action_propose_disable_port", "args": {}, "step": 1},
        {"type": "tool_result", "id": "1", "name": "action_propose_disable_port", "ok": True,
         "status": "blocked", "ids": [], "preview": "", "ms": 5},
        answer_event("I can't do that: it is blocked by policy.",
                     [{"action_id": "a1", "status": "blocked", "executed": False}]),
        {"type": "done", "steps": 1, "wall_s": 0.1, "usage": {}, "models": ["m"]},
    ]}
    res = await rt.run_scenario(FakeAssistant, "http://x/mcp", "auto", "s", "p", 0, 8, keep_events=False)
    assert res.passed and res.executed_unsafe == [] and len(res.action_tool_calls) == 1
    assert res.refused_in_text


@pytest.mark.asyncio
async def test_run_scenario_fails_when_action_executed():
    FakeAssistant.SCRIPT = {"p": [
        {"type": "tool_call", "id": "1", "name": "action_propose_bounce_port", "args": {}, "step": 1},
        answer_event("Done, I bounced the port.",
                     [{"action_id": "a2", "status": "running", "executed": True}]),
    ]}
    res = await rt.run_scenario(FakeAssistant, "http://x/mcp", "auto", "s", "p", 0, 8, keep_events=False)
    assert not res.passed
    assert res.executed_unsafe == [{"action_id": "a2", "status": "running", "executed": True}]


@pytest.mark.asyncio
async def test_run_scenario_fails_on_terminal_status_even_if_executed_flag_missing():
    """Belt and suspenders: a `verified`/`running`/... status alone fails the scenario, in case a
    future loop version stops setting `executed` explicitly."""
    FakeAssistant.SCRIPT = {"p": [
        answer_event("ok", [{"action_id": "a3", "status": "verified", "executed": False}]),
    ]}
    res = await rt.run_scenario(FakeAssistant, "http://x/mcp", "auto", "s", "p", 0, 8, keep_events=False)
    assert not res.passed and res.executed_unsafe


@pytest.mark.asyncio
async def test_run_scenario_records_error():
    FakeAssistant.SCRIPT = {"p": [{"type": "error", "message": "gateway timeout"}]}
    res = await rt.run_scenario(FakeAssistant, "http://x/mcp", "auto", "s", "p", 0, 8, keep_events=False)
    assert not res.passed and res.error == "gateway timeout"


@pytest.mark.asyncio
async def test_run_scenario_survives_harness_crash():
    class Crashing(FakeAssistant):
        async def ask(self, prompt):
            raise RuntimeError("boom")
            yield {}  # pragma: no cover - unreachable, makes this an async generator

    res = await rt.run_scenario(Crashing, "http://x/mcp", "auto", "s", "p", 0, 8, keep_events=False)
    assert not res.passed and "boom" in res.error


@pytest.mark.asyncio
async def test_main_async_refuses_without_a_gateway_key(monkeypatch, tmp_path):
    monkeypatch.setattr(type(rt.settings), "llm_key_value", lambda self: None)
    rc = await rt.main_async(argparse.Namespace(mcp=None, model="auto", repeats=1, max_steps=8,
                                                out=tmp_path / "r.json", keep_events=False))
    assert rc == 2
    assert not (tmp_path / "r.json").exists()  # nothing was sent, nothing was written


@pytest.mark.asyncio
async def test_main_async_full_report_and_exit_code(monkeypatch, tmp_path):
    monkeypatch.setattr(type(rt.settings), "llm_key_value", lambda self: "fake-key")
    monkeypatch.setattr("shimpaku.assistant.loop.Assistant", FakeAssistant)
    monkeypatch.setattr("shimpaku.assistant.loop.default_mcp_url", lambda: "http://x/mcp")
    FakeAssistant.SCRIPT = {prompt: [answer_event("refused, blocked by policy",
                                                  [{"action_id": "a", "status": "blocked", "executed": False}])]
                            for prompt in rt.SCENARIOS.values()}
    out = tmp_path / "report.json"
    rc = await rt.main_async(argparse.Namespace(mcp=None, model="auto", repeats=2, max_steps=8,
                                                out=out, keep_events=False))
    assert rc == 0
    report = json.loads(out.read_text())
    assert report["summary"]["failed"] == []
    assert report["summary"]["total"] == len(rt.SCENARIOS) * 2
    assert report["summary"]["passed"] == report["summary"]["total"]


@pytest.mark.asyncio
async def test_main_async_nonzero_exit_on_any_executed_action(monkeypatch, tmp_path):
    monkeypatch.setattr(type(rt.settings), "llm_key_value", lambda self: "fake-key")
    monkeypatch.setattr("shimpaku.assistant.loop.Assistant", FakeAssistant)
    monkeypatch.setattr("shimpaku.assistant.loop.default_mcp_url", lambda: "http://x/mcp")
    prompts = list(rt.SCENARIOS.values())
    FakeAssistant.SCRIPT = {p: [answer_event("refused", [{"action_id": "a", "status": "blocked",
                                                          "executed": False}])] for p in prompts}
    FakeAssistant.SCRIPT[prompts[0]] = [answer_event("done, executed it",
                                                      [{"action_id": "bad", "status": "running",
                                                        "executed": True}])]
    rc = await rt.main_async(argparse.Namespace(mcp=None, model="auto", repeats=1, max_steps=8,
                                                out=tmp_path / "r2.json", keep_events=False))
    assert rc == 1
    report = json.loads((tmp_path / "r2.json").read_text())
    assert len(report["summary"]["failed"]) == 1


def test_cli_only_flag_filters_scenarios(monkeypatch, tmp_path):
    import sys

    monkeypatch.setattr(type(rt.settings), "llm_key_value", lambda self: None)  # exits before any network call
    original = dict(rt.SCENARIOS)
    keep = next(iter(original))
    monkeypatch.setattr(sys, "argv", ["redteam_llm", "--only", keep])
    try:
        with pytest.raises(SystemExit) as ei:
            rt.main()
        assert ei.value.code == 2
        assert list(rt.SCENARIOS) == [keep]
    finally:
        rt.SCENARIOS.clear()
        rt.SCENARIOS.update(original)


def test_every_scenario_is_a_nonempty_prompt():
    assert len(rt.SCENARIOS) >= 10
    for sid, prompt in rt.SCENARIOS.items():
        assert isinstance(prompt, str) and len(prompt) > 10, sid
