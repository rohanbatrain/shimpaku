from shimpaku import about
from shimpaku.assistant import prompts
from shimpaku.config import settings


def test_facts_name_the_creators_the_repo_and_the_configured_models(monkeypatch):
    monkeypatch.setattr(settings, "llm_models", "triage=model-a,diagnose=model-b")
    f = about.facts()
    assert "Aryan and Rohan" in f and settings.repo_url in f
    assert "model-a" in f and "model-b" in f
    assert "B-assistant" in f and "not people" in f


def test_every_assistant_mode_gets_the_facts():
    import inspect
    from shimpaku.assistant import loop
    assert "about.facts()" in inspect.getsource(loop.Assistant.ask)


def test_guide_prompt_carries_the_public_guardrails():
    g = prompts.GUIDE
    assert "Never invent facts" in g and "Never reveal" in g and "personal data" in g and "🔓" in g
