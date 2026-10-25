import json

import httpx
import pytest
import respx

from shimpaku.assistant import loop
from shimpaku.assistant.fixture import SCENARIOS, MockLab
from shimpaku.assistant.llm import LLM, LLMError
from shimpaku.assistant.loop import Assistant, claims_execution, parse_answer
from shimpaku.config import settings

from .scripted import BAD_S09, F, ScriptedModel, mock_gateway, sse

SC = {s["id"].split("-")[0]: s for s in SCENARIOS}
KEY = "sk-test-not-a-real-key-123"


def make(sid, model=None, **kw):
    lab = MockLab(SC[sid])
    a = Assistant(lab.server, model=kw.pop("role", "diagnose"),
                  llm_factory=lambda name: LLM(name, key=KEY, retries=1), **kw)
    return lab, a


async def collect(a, q):
    return [ev async for ev in a.ask(q)]


@pytest.mark.asyncio
async def test_s01_stream_trace_and_structured_answer():
    m = ScriptedModel()
    lab, a = make("S01")
    with mock_gateway(m):
        evs = await collect(a, SC["S01"]["prompt"])
    types = [e["type"] for e in evs]
    assert types[0] == "route" and types[-1] == "done"
    assert "token" in types and "tool_call" in types and "tool_result" in types
    ans = next(e for e in evs if e["type"] == "answer")["answer"]
    assert "ge-0/0/3" in ans["root_cause"] and ans["issue"] and ans["recommendations"]
    assert {"kind": "incident", "id": "inc-s01"} in ans["evidence_ids"]
    assert {"kind": "action", "id": "act-s01-1"} in ans["evidence_ids"]
    assert ans["actions"] == [{"action_id": "act-s01-1", "status": "awaiting_approval", "executed": False}]
    assert ans["corrected"] is False
    # the key goes in the header and nowhere else
    assert all(KEY not in json.dumps(e) for e in evs)
    assert m.requests[0]["model"] == settings.models()["diagnose"]
    # action_approve is never offered to the model
    names = {t["function"]["name"] for t in m.requests[0]["tools"]}
    assert "action_approve" not in names and "action_propose_rollback" in names


@pytest.mark.asyncio
async def test_never_claims_execution_without_action_get():
    m = ScriptedModel({"S09": BAD_S09})
    lab, a = make("S09")
    with mock_gateway(m):
        evs = await collect(a, SC["S09"]["prompt"])
    ans = next(e for e in evs if e["type"] == "answer")
    assert ans["answer"]["corrected"] is True
    assert "no action has been executed" in ans["text"]
    # both injected proposals hit the guardrail in code (fabric ports)
    blocked = [c for c in lab.calls if c.get("guardrail") == "blocked"]
    assert len(blocked) == 2


@pytest.mark.asyncio
async def test_execution_claim_allowed_when_action_get_says_so():
    lab, a = make("S01")
    lab.proposals["act-x"] = {"id": "act-x", "status": "verified"}
    script = {"S01": [[("action_get", {"action_id": "act-x"})],
                      F("x", "x", "sw1 ge-0/0/3 stuck", "The bounce was executed and has been validated.", "act-x")]}
    with mock_gateway(ScriptedModel(script)):
        evs = await collect(a, SC["S01"]["prompt"])
    assert next(e for e in evs if e["type"] == "answer")["answer"]["corrected"] is False


@pytest.mark.asyncio
async def test_step_cap_forces_final_answer():
    m = ScriptedModel(forever=("sle_get_summary", {}))
    lab, a = make("S07", max_steps=3)
    with mock_gateway(m):
        evs = await collect(a, SC["S07"]["prompt"])
    done = evs[-1]
    assert done["steps"] == 3
    assert any(e["type"] == "answer" for e in evs)
    assert "tools" not in m.requests[-1]  # last request offers no tools


@pytest.mark.asyncio
async def test_auto_routing_escalates_triage_before_a_proposal():
    m = ScriptedModel({"S07": [[("action_propose_bounce_port", {"device": "sw1", "ifname": "ge-0/0/3", "reason": "x"})],
                               [("action_propose_bounce_port", {"device": "sw1", "ifname": "ge-0/0/3", "reason": "x"})],
                               F("a", "b", "c", "d", "e")]})
    lab, a = make("S07", role="auto")
    with mock_gateway(m):
        evs = await collect(a, SC["S07"]["prompt"])
    routes = [e for e in evs if e["type"] == "route"]
    assert routes[0]["role"] == "triage" and routes[1]["role"] == "diagnose"
    assert m.requests[0]["model"] == settings.models()["triage"] and m.requests[1]["model"] == settings.models()["diagnose"]


@pytest.mark.asyncio
async def test_unknown_tool_and_bad_args_go_back_to_the_model():
    m = ScriptedModel({"S07": [[("fabric_nuke", {})], [("iface_get_errors", {"device": "leaf9", "ifname": "ge-0/0/0"})],
                               F("a", "b", "none found", "d", "e")]})
    lab, a = make("S07")
    with mock_gateway(m):
        evs = await collect(a, SC["S07"]["prompt"])
    res = [e for e in evs if e["type"] == "tool_result"]
    assert [r["ok"] for r in res] == [False, False]
    assert any(e["type"] == "answer" for e in evs)


@pytest.mark.asyncio
async def test_gateway_retry_then_success_and_key_scrubbed():
    calls = {"n": 0}

    def side(request):
        calls["n"] += 1
        assert request.headers["authorization"] == f"Bearer {KEY}"
        if calls["n"] == 1:
            return httpx.Response(429, text=f"slow down {KEY}")
        return httpx.Response(200, content=sse(content="Issue: none"), headers={"content-type": "text/event-stream"})

    with respx.mock() as r:
        r.post("http://127.0.0.1:4000/v1/chat/completions").mock(side_effect=side)
        llm = LLM("m", key=KEY, retries=2)
        evs = [e async for e in llm.stream([{"role": "user", "content": "hi"}])]
    assert evs[-1][1].content == "Issue: none" and calls["n"] == 2

    with respx.mock() as r:
        r.post("http://127.0.0.1:4000/v1/chat/completions").mock(return_value=httpx.Response(401, text=f"bad key {KEY}"))
        with pytest.raises(LLMError) as ei:
            [e async for e in LLM("m", key=KEY).stream([{"role": "user", "content": "hi"}])]
    assert KEY not in str(ei.value) and "401" in str(ei.value)
    assert KEY not in repr(LLM("m", key=KEY))


@pytest.mark.asyncio
async def test_text_form_tool_call_becomes_a_real_call_and_is_not_streamed():
    # Nemotron writes the call into content, split across chunks, instead of delta.tool_calls
    parts = ["Checking. <tool", "_call> <function=inventory_list> <parameter=site> campus ",
             "</parameter> </function> </tool_call>"]
    body = b"".join(b"data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": p}}]}).encode() + b"\n\n"
                    for p in parts) + b"data: [DONE]\n\n"
    with respx.mock() as r:
        r.post("http://127.0.0.1:4000/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=body, headers={"content-type": "text/event-stream"}))
        evs = [e async for e in LLM("m", key=KEY).stream([{"role": "user", "content": "hi"}])]
    shown = "".join(v for k, v in evs if k == "token")
    turn = evs[-1][1]
    assert "<" not in shown and shown == "Checking. "
    assert [(c["name"], json.loads(c["arguments"])) for c in turn.tool_calls] == [("inventory_list", {"site": "campus"})]
    assert turn.content == "Checking." and turn.tool_calls[0]["id"] == "call_0"


@pytest.mark.asyncio
async def test_gateway_down_yields_error_event_not_exception():
    lab, a = make("S07")
    with respx.mock() as r:
        r.post("http://127.0.0.1:4000/v1/chat/completions").mock(side_effect=httpx.ConnectError("refused"))
        evs = await collect(a, "hi")
    assert any(e["type"] == "error" for e in evs) and evs[-1]["type"] == "done"


def test_parse_answer_and_claims():
    t = "**Issue:** a\n**Impact**: b\nLikely root cause: c\n- Recommendations: d\nEvidence: e"
    assert parse_answer(t) == {"issue": "a", "impact": "b", "root_cause": "c", "recommendations": "d", "evidence": "e"}
    assert claims_execution("I have bounced sw1 ge-0/0/3.")
    assert claims_execution("The port was successfully disabled.")
    assert not claims_execution("The port has not been bounced; a bounce is proposed.")
    assert not claims_execution("Nothing was executed yet.")
    assert loop.default_mcp_url().endswith("/mcp")


def test_a_null_error_field_is_success_not_failure():
    """Every MCP read returns {"error": null, ...} on success; treating the key's presence as a
    failure marked all of Shim's tool calls failed and made it retry them (found live 2026-10-25)."""
    import inspect
    src = inspect.getsource(loop.Assistant.ask)
    assert 'payload.get("error")' in src and '"error" in payload' not in src
