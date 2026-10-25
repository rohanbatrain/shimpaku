"""LiteLLM gateway client (OpenAI chat-completions format, streamed).

The ONLY way Shim talks to a model: the operator's LiteLLM proxy at ``settings.llm_base``.
There is no local-model mode. The key comes from ``settings.llm_key_value()`` and is never
printed, logged, or put in an exception message.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from shimpaku.config import settings


class LLMError(RuntimeError):
    """Gateway failure. The message never contains the key."""


@dataclass
class Turn:
    """One completed model turn."""

    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)  # [{id, name, arguments(str)}]
    finish_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


class LLM:
    def __init__(
        self,
        model: str,
        *,
        base: str | None = None,
        key: str | None = None,
        timeout_s: float = 120.0,
        retries: int = 2,
        temperature: float = 0.3,
        seed: int | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self.base = (base or settings.llm_base).rstrip("/")
        self._key = key if key is not None else settings.llm_key_value()
        self.timeout_s = timeout_s
        self.retries = retries
        self.temperature = temperature
        self.seed = seed
        self._transport = transport

    def __repr__(self) -> str:  # never show the key
        return f"LLM(model={self.model!r}, base={self.base!r})"

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self._key:
            h["Authorization"] = f"Bearer {self._key}"
        return h

    async def stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> AsyncIterator[tuple[str, Any]]:
        """Yield ("token", str) while streaming, then exactly one ("turn", Turn)."""
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": self.temperature,
        }
        if self.seed is not None:
            body["seed"] = self.seed
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        attempt = 0
        while True:
            try:
                async for ev in self._once(body):
                    yield ev
                return
            except _Retryable as e:
                attempt += 1
                if attempt > self.retries:
                    raise LLMError(str(e)) from None
                await asyncio.sleep(min(2.0 * 2 ** (attempt - 1), 30.0))

    async def _once(self, body: dict[str, Any]) -> AsyncIterator[tuple[str, Any]]:
        turn = Turn()
        calls: dict[int, dict[str, Any]] = {}
        emitted = 0
        timeout = httpx.Timeout(self.timeout_s, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
            try:
                async with client.stream(
                    "POST", f"{self.base}/v1/chat/completions", headers=self._headers(), json=body
                ) as r:
                    if r.status_code != 200:
                        text = (await r.aread()).decode(errors="replace")[:400]
                        msg = f"gateway HTTP {r.status_code} for model {self.model}: {_scrub(text, self._key)}"
                        if r.status_code in (408, 429, 500, 502, 503, 504):
                            raise _Retryable(msg)
                        raise LLMError(msg)
                    async for line in r.aiter_lines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if chunk.get("error"):
                            raise _Retryable(f"gateway stream error: {_scrub(str(chunk['error'])[:300], self._key)}")
                        if chunk.get("usage"):
                            turn.usage = {k: v for k, v in chunk["usage"].items() if isinstance(v, int)}
                        for ch in chunk.get("choices") or []:
                            d = ch.get("delta") or {}
                            if d.get("content"):
                                turn.content += d["content"]
                                # hold back anything that may open a text-form tool call
                                upto = _safe_prefix(turn.content)
                                if upto > emitted:
                                    yield ("token", turn.content[emitted:upto])
                                    emitted = upto
                            for tc in d.get("tool_calls") or []:
                                slot = calls.setdefault(tc.get("index", len(calls)), {"id": None, "name": "", "arguments": ""})
                                if tc.get("id"):
                                    slot["id"] = tc["id"]
                                fn = tc.get("function") or {}
                                if fn.get("name"):
                                    slot["name"] += fn["name"]
                                if fn.get("arguments"):
                                    a = fn["arguments"]
                                    slot["arguments"] += a if isinstance(a, str) else json.dumps(a)
                            if ch.get("finish_reason"):
                                turn.finish_reason = ch["finish_reason"]
            except (httpx.TimeoutException, httpx.TransportError) as e:
                raise _Retryable(f"gateway transport error for model {self.model}: {type(e).__name__}") from None
        if not calls:
            # Some models (Nemotron, Qwen-coder) write the call into content instead of tool_calls
            text_calls, rest = _text_tool_calls(turn.content)
            if text_calls:
                calls = dict(enumerate(text_calls))
                turn.content = rest
                turn.finish_reason = "tool_calls"
        if not calls and len(turn.content) > emitted:
            yield ("token", turn.content[emitted:])  # held back but was not a call after all
        for i, slot in sorted(calls.items()):
            slot["id"] = slot["id"] or f"call_{i}"
            turn.tool_calls.append(slot)
        yield ("turn", turn)


class _Retryable(Exception):
    pass


def _scrub(text: str, key: str | None) -> str:
    return text.replace(key, "***") if key else text


_OPENERS = ("<tool_call>", "<toolcall>")
_BLOCK = re.compile(r"<(tool_?call)>(.*?)(?:</\1>|$)", re.S | re.I)
_FUNC = re.compile(r"<function=([\w.-]+)>(.*?)(?:</function>|$)", re.S)
_PARAM = re.compile(r"<parameter=([\w.-]+)>(.*?)</parameter>", re.S)


def _safe_prefix(text: str) -> int:
    """How much of streamed content can be shown: up to a text-form tool call, or a partial opener."""
    low = text.lower()
    cut = min((i for i in (low.find(o) for o in _OPENERS) if i >= 0), default=-1)
    if cut >= 0:
        return cut
    for n in range(min(len(low), max(map(len, _OPENERS)) - 1), 0, -1):
        if any(o.startswith(low[-n:]) for o in _OPENERS):
            return len(text) - n
    return len(text)


def _param_value(raw: str) -> Any:
    v = raw.strip()
    try:
        return json.loads(v)
    except json.JSONDecodeError:
        return v


def _text_tool_calls(content: str) -> tuple[list[dict[str, Any]], str]:
    """Parse tool calls a model wrote as text: ``<tool_call><function=f><parameter=k>v</parameter>``
    (Qwen-coder/Nemotron XML), ``<tool_call>{"name":..,"arguments":..}`` (Hermes), or
    ``<TOOLCALL>[{...}]`` (Nemotron). Returns the calls and the content with the markup removed."""
    found: list[dict[str, Any]] = []
    for m in _BLOCK.finditer(content):
        body = m.group(2).strip()
        for f in _FUNC.finditer(body):
            args = {k: _param_value(v) for k, v in _PARAM.findall(f.group(2))}
            found.append({"name": f.group(1), "arguments": json.dumps(args)})
        if _FUNC.search(body):
            continue
        try:
            obj = json.loads(body)
        except json.JSONDecodeError:
            continue
        for c in obj if isinstance(obj, list) else [obj]:
            if isinstance(c, dict) and isinstance(c.get("name"), str):
                a = c.get("arguments", c.get("parameters", {}))
                found.append({"name": c["name"], "arguments": a if isinstance(a, str) else json.dumps(a)})
    if not found:
        return [], content
    return [{"id": None, **c} for c in found], _BLOCK.sub("", content).strip()
