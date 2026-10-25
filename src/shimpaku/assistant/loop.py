"""Shim's plain tool loop (R7 §3): MCP client of shimpakud /mcp + LLM via the LiteLLM gateway.

``Assistant.ask(question)`` is an async generator of JSON-serialisable events; B-web exposes it at
POST /api/assistant/ask, the CLI prints it. Event types, in order:

  {"type": "route",       "role", "model", "reason"}            (again on every escalation)
  {"type": "token",       "text"}                                (streamed model text)
  {"type": "tool_call",   "id", "name", "args", "step"}
  {"type": "tool_result", "id", "name", "ok", "status", "ids", "preview", "ms"}
  {"type": "answer",      "text", "answer": {issue, impact, root_cause, recommendations,
                           evidence: [{"kind","id"}], actions: [{"action_id","status","executed"}],
                           tools_used: [...], corrected: bool}}
  {"type": "error",       "message"}
  {"type": "done",        "steps", "wall_s", "usage", "models"}

Safety lives in the MCP server and executor; this loop only adds: a step cap, timeouts, no
action_approve tool, and a runtime check that the answer never claims an action ran unless
action_get said so.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from mcp import Client

from shimpaku.assistant import prompts, routing
from shimpaku.assistant.llm import LLM, LLMError
from shimpaku.config import settings

HIDDEN_TOOLS = {"action_approve"}  # approvals are dashboard-only (CONTRACT)
# real shimpaku.actions.state vocabulary (src/shimpaku/actions/state.py LIVE/TERMINAL, minus the
# pre-approval states): once an action reaches one of these, something has actually run or is
# running on the device. "blocked", "awaiting_approval", "cancelled", "rejected", "open" and
# "proposed" are all pre-execution.
EXECUTED_STATES = {"automatic", "approved", "running", "validating", "verified", "recurred", "failed"}
ACTION_TOOLS_PREFIX = "action_"
MAX_RESULT_CHARS = 6000
_ID_KEYS = {"incident_id": "incident"}
_EXEC_CLAIM = re.compile(
    r"\b(?:i|we|shim)\s+(?:have\s+|just\s+|already\s+)?(?:bounced|disabled|rolled\s+back|applied|executed|re-?enabled|added)\b"
    r"|\b(?:has|have)\s+(?:now\s+|already\s+)?been\s+(?:bounced|disabled|rolled\s+back|applied|executed|fixed|completed)\b"
    r"|\b(?:was|were)\s+(?:successfully\s+)?(?:bounced|disabled|rolled\s+back|applied|executed)\b"
    r"|\bsuccessfully\s+(?:bounced|disabled|rolled\s+back|applied|executed)\b",
    re.I,
)
_NEGATED = re.compile(r"(\bnot\b|\bnever\b|n't\b|\bno\b|\bnothing\b|\bnone\b)[^.\n]{0,25}$", re.I)


def default_mcp_url() -> str:
    return f"http://{settings.bind}:{settings.port}/mcp"


def mcp_tools_to_openai(tools: list[Any]) -> list[dict[str, Any]]:
    out = []
    for t in tools:
        if t.name in HIDDEN_TOOLS:
            continue
        schema = dict(t.input_schema or {"type": "object", "properties": {}})
        schema.pop("title", None)
        out.append({"type": "function", "function": {
            "name": t.name, "description": (t.description or t.name)[:1024], "parameters": schema}})
    return out


def _collect_ids(obj: Any, out: list[tuple[str, str]]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _ID_KEYS and isinstance(v, str | int):
                out.append((_ID_KEYS[k], str(v)))
            else:
                _collect_ids(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_ids(v, out)


def parse_answer(text: str) -> dict[str, str]:
    """Split the five-section answer. Missing sections stay empty; the whole text is kept elsewhere."""
    names = "|".join(f"(?P<{k}>{v})" for k, v in prompts.HEADERS.items())
    rx = re.compile(rf"^[ \t>*#\-]*\**\s*(?:{names})\s*\**\s*:\s*\**", re.I | re.M)
    out = {k: "" for k in prompts.SECTIONS}
    marks = [(m.start(), m.end(), next(k for k in prompts.SECTIONS if m.group(k))) for m in rx.finditer(text)]
    for i, (_s, e, k) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        if not out[k]:
            out[k] = text[e:end].strip().strip("*").strip()
    return out


def claims_execution(text: str) -> bool:
    for m in _EXEC_CLAIM.finditer(text):
        if not _NEGATED.search(text[max(0, m.start() - 40):m.start()]):
            return True
    return False


class Assistant:
    def __init__(
        self,
        mcp_target: Any = None,
        *,
        model: str | None = "auto",
        llm_factory: Callable[[str], LLM] | None = None,
        max_steps: int = 8,
        tool_timeout_s: float = 30.0,
        deadline_s: float = 300.0,
        seed: int | None = None,
        kb: bool = True,
    ) -> None:
        self.kb = kb
        self.mcp_target = mcp_target if mcp_target is not None else default_mcp_url()
        self.model = model
        self.llm_factory = llm_factory or (lambda name: LLM(name, seed=seed))
        self.max_steps = max_steps
        self.tool_timeout_s = tool_timeout_s
        self.deadline_s = deadline_s

    async def ask(self, question: str, history: list[dict[str, Any]] | None = None) -> AsyncIterator[dict[str, Any]]:
        t0 = time.perf_counter()
        auto = self.model in (None, "", "auto")
        if auto:
            role = routing.initial_role(question)
            model = routing.model_for(role)
        else:
            role, model = routing.resolve(self.model)
        yield {"type": "route", "role": role, "model": model, "reason": "auto" if auto else "requested"}
        models_used = [model]
        llm = self.llm_factory(model)
        usage: dict[str, int] = {}
        actions: dict[str, dict[str, Any]] = {}  # action_id -> {status, executed}
        evidence: list[tuple[str, str]] = []
        tools_used: list[str] = []
        observed: list[str] = [question]
        invalid = 0
        steps = 0
        final = ""
        try:
            async with Client(self.mcp_target, read_timeout_seconds=self.tool_timeout_s) as mcp:
                listed = await mcp.list_tools()
                tools = mcp_tools_to_openai(listed.tools)
                known = {t["function"]["name"] for t in tools}
                system = prompts.SYSTEM
                if self.kb:
                    try:
                        from shimpaku import kb
                        bg = kb.grounding(question)
                    except Exception:  # noqa: BLE001 — the knowledge base is an aid, never a blocker
                        bg = None
                    if bg:
                        system = f"{system}\n\n{bg}"
                messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
                messages += list(history or [])
                messages.append({"role": "user", "content": question})
                while True:
                    if steps >= self.max_steps:
                        messages.append({"role": "user", "content": (
                            "Step limit reached. Do not call more tools. Give your final answer now in the five sections, "
                            "saying what you could not check.")})
                    if time.perf_counter() - t0 > self.deadline_s:
                        yield {"type": "error", "message": f"deadline {self.deadline_s:.0f}s reached"}
                        break
                    turn = None
                    remaining = self.deadline_s - (time.perf_counter() - t0)
                    try:
                        # A hard wall-clock bound on this turn, independent of per-chunk read
                        # timeouts: a slow dribble of bytes must not let one turn outlast the
                        # question's overall deadline.
                        async with asyncio.timeout(max(1.0, remaining)):
                            async for kind, val in llm.stream(messages, tools if steps < self.max_steps else None):
                                if kind == "token":
                                    yield {"type": "token", "text": val}
                                else:
                                    turn = val
                    except TimeoutError:
                        yield {"type": "error", "message": f"deadline {self.deadline_s:.0f}s reached mid-turn (model {model})"}
                        break
                    assert turn is not None
                    for k, v in turn.usage.items():
                        usage[k] = usage.get(k, 0) + v
                    calls = turn.tool_calls if steps < self.max_steps else []
                    # auto routing: a first-pass triage model must not author proposals or flail
                    if auto and role == "triage" and any(c["name"].startswith("action_propose") for c in calls):
                        role, model = "diagnose", routing.model_for("diagnose")
                        llm = self.llm_factory(model)
                        models_used.append(model)
                        yield {"type": "route", "role": role, "model": model, "reason": "action proposal needs diagnose"}
                        continue
                    asst: dict[str, Any] = {"role": "assistant", "content": turn.content or None}
                    if calls:
                        asst["tool_calls"] = [{"id": c["id"], "type": "function",
                                               "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                                              for c in calls]
                    messages.append(asst)
                    if not calls:
                        final = turn.content or ""
                        break
                    steps += 1
                    for c in calls:
                        try:
                            args = json.loads(c["arguments"] or "{}")
                            if not isinstance(args, dict):
                                raise ValueError("arguments must be an object")
                        except (json.JSONDecodeError, ValueError) as e:
                            args, bad = {}, f"invalid JSON arguments: {e}"
                        else:
                            bad = None if c["name"] in known else f"unknown tool {c['name']!r}"
                        yield {"type": "tool_call", "id": c["id"], "name": c["name"], "args": args, "step": steps}
                        ts = time.perf_counter()
                        if bad:
                            payload: Any = {"error": bad}
                            ok, status = False, "invalid"
                        else:
                            try:
                                res = await mcp.call_tool(c["name"], args)
                                payload = res.structured_content
                                if payload is None:
                                    payload = "\n".join(getattr(x, "text", "") for x in res.content)
                                # a read returns {"error": null, ...} on success: only a non-empty error is a failure
                                ok = not res.is_error and not (isinstance(payload, dict) and payload.get("error"))
                                status = (payload.get("status") if isinstance(payload, dict) else None) or ("ok" if ok else "error")
                            except Exception as e:  # noqa: BLE001 — timeouts and transport errors go back to the model
                                payload, ok, status = {"error": f"{type(e).__name__}: tool call failed"}, False, "error"
                        if not ok:
                            invalid += 1
                        tools_used.append(c["name"])
                        text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
                        observed.append(text)
                        ids: list[tuple[str, str]] = []
                        _collect_ids(payload, ids)
                        is_action_tool = c["name"].startswith(ACTION_TOOLS_PREFIX) and c["name"] != "action_approve"
                        if is_action_tool and isinstance(payload, dict) and payload.get("id"):
                            aid = str(payload["id"])
                            st = str(payload.get("status") or "").lower()
                            # action_get is the only source of truth for "did it execute"; a fresh
                            # proposal from action_propose_* is never executed yet, regardless of status.
                            executed = c["name"] == "action_get" and st in EXECUTED_STATES
                            actions[aid] = {"status": st, "executed": executed}
                            ids.append(("action", aid))
                        evidence += [i for i in ids if i not in evidence]
                        yield {"type": "tool_result", "id": c["id"], "name": c["name"], "ok": ok, "status": status,
                               "ids": [{"kind": k, "id": v} for k, v in ids], "preview": text[:300],
                               "ms": round(1000 * (time.perf_counter() - ts))}
                        messages.append({"role": "tool", "tool_call_id": c["id"], "content": text[:MAX_RESULT_CHARS]})
                    if auto and invalid >= 2 and role != "escalate":
                        role = routing.NEXT[role]
                        model = routing.model_for(role)
                        llm = self.llm_factory(model)
                        models_used.append(model)
                        invalid = 0
                        yield {"type": "route", "role": role, "model": model, "reason": "repeated invalid tool calls"}
        except LLMError as e:
            yield {"type": "error", "message": str(e)}
        except Exception as e:  # noqa: BLE001 — the API must always get a terminal event
            while isinstance(e, BaseExceptionGroup) and len(e.exceptions) == 1:
                e = e.exceptions[0]
            if isinstance(e, LLMError):
                yield {"type": "error", "message": str(e)}
            else:
                yield {"type": "error", "message": f"{type(e).__name__}: {e}"[:300]}

        if final:
            corrected = False
            if claims_execution(final) and not any(a["executed"] for a in actions.values()):
                final = final.rstrip() + "\n\n" + prompts.EXECUTION_CORRECTION
                corrected = True
                yield {"type": "token", "text": "\n\n" + prompts.EXECUTION_CORRECTION}
            ans: dict[str, Any] = parse_answer(final)
            ans["evidence_ids"] = [{"kind": k, "id": v} for k, v in evidence]
            ans["actions"] = [{"action_id": k, **v} for k, v in actions.items()]
            ans["tools_used"] = tools_used
            ans["corrected"] = corrected
            yield {"type": "answer", "text": final, "answer": ans}
        yield {"type": "done", "steps": steps, "wall_s": round(time.perf_counter() - t0, 2), "usage": usage,
               "models": models_used}


async def ask_text(question: str, **kw: Any) -> dict[str, Any]:
    """Convenience: run to completion, return the answer event (or the error)."""
    last: dict[str, Any] = {}
    async for ev in Assistant(**kw).ask(question):
        if ev["type"] in ("answer", "error"):
            last = ev
    return last
