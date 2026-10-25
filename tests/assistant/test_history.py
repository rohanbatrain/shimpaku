import asyncio

from shimpaku import service
from shimpaku.assistant import loop


def test_assistant_ask_passes_recent_turns_as_chat_history(monkeypatch):
    seen = {}

    class Fake:
        def __init__(self, *a, **k):
            pass

        async def ask(self, question, history=None):
            seen["q"], seen["h"] = question, history
            yield {"type": "done"}

    monkeypatch.setattr(loop, "Assistant", Fake)
    monkeypatch.setattr(service, "_mcp_server", object())
    hist = [{"q": "how is leaf1?", "a": "leaf1 is amber: CPU 91%."}, {"q": "", "a": "dropped: no question"}]

    async def run():
        return [e async for e in service.assistant_ask("and now?", "admin", history=hist)]

    evs = asyncio.run(run())
    assert evs[-1]["type"] == "done"
    assert seen["q"] == "and now?"
    assert seen["h"] == [{"role": "user", "content": "how is leaf1?"}, {"role": "assistant", "content": "leaf1 is amber: CPU 91%."}]


def test_guide_mode_offers_only_the_knowledge_base(monkeypatch):
    seen = {}

    class Fake:
        def __init__(self, *a, **k):
            seen.update(k)

        async def ask(self, question, history=None):
            yield {"type": "done"}

    monkeypatch.setattr(loop, "Assistant", Fake)
    monkeypatch.setattr(service, "_mcp_server", object())

    async def run():
        return [e async for e in service.assistant_ask("hi", "public", guide=True)]

    asyncio.run(run())
    assert seen["allow_tools"] == {"kb_search", "kb_read"} and seen["model"] == "triage" and seen["max_steps"] == 3
    from shimpaku.assistant import prompts
    assert seen["system"] == prompts.GUIDE and "🔓" in prompts.GUIDE
