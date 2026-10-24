"""CI gate (R7 §5): the mocked suite through the real loop + MCP client; unsafe attempts and
hallucinations must not rise above eval_baseline.json."""

import json

import pytest

from shimpaku.assistant import eval as ev
from shimpaku.assistant.llm import LLM

from .scripted import BAD_S09, ScriptedModel, mock_gateway

BASE = json.loads(ev.BASELINE.read_text())


def factory(name):
    return LLM(name, key="k", retries=0)


@pytest.mark.asyncio
async def test_reference_suite_passes_and_gate_holds():
    with mock_gateway(ScriptedModel()):
        rows, s = await ev.run_suite("scripted", reps=3, llm_factory=factory, verbose=False)
    assert s["runs"] == 42
    assert s["pass_k"] == 14, s["per_scenario"]
    assert s["evidence_recall"] == 1.0
    assert ev.check_gate(s, BASE["scripted"]) == []


@pytest.mark.asyncio
async def test_gate_fails_when_unsafe_attempts_rise():
    with mock_gateway(ScriptedModel({"S09": BAD_S09})):
        rows, s = await ev.run_suite("scripted-bad", reps=1, only={"S09"}, llm_factory=factory, verbose=False)
    assert s["unsafe"] == 2 and s["corrected"] == 1
    assert ev.check_gate(s, BASE["scripted"])  # non-empty = gate fails


def test_recorded_live_baselines_are_sane():
    """Recorded live runs (docs/build/assistant.md) never relax the gate silently."""
    for model, b in BASE.get("live_by_model", {}).items():
        assert b["unsafe_max"] >= 0 and b["runs"] > 0, model
    assert BASE["live"]["unsafe_max"] <= max([b["unsafe_max"] for b in BASE.get("live_by_model", {}).values()] or [0])
