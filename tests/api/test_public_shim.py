"""Shim's public guide: anyone may ask, only about the documents, capped, CORS for the landing page."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from shimpaku.api import demo
from shimpaku.api.app import create_app
from shimpaku.config import settings

LANDING = "https://shimpaku.example.in"


def _app(monkeypatch, enabled=True):
    seen = []

    async def ask_fn(question, principal, context, guide=False, history=None, llm_key=None, role=None, lease_id=None):
        seen.append({"q": question, "guide": guide, "history": history, "principal": principal})
        yield {"type": "token", "text": "Hi! 🔓 request a live session"}
        yield {"type": "done"}
    monkeypatch.setattr(settings, "public_shim", enabled)
    monkeypatch.setattr(settings, "public_shim_per_ip", 3)
    monkeypatch.setattr(settings, "public_cors_origins", LANDING)
    return TestClient(create_app(demo, auth_mode="local", assistant=ask_fn)), seen


def test_off_by_default(monkeypatch):
    c, seen = _app(monkeypatch, enabled=False)
    r = c.post("/api/assistant/public", content=json.dumps({"question": "hi"}))
    assert r.status_code == 503 and not seen


def test_anonymous_ask_is_guide_mode_with_cors_and_text_plain(monkeypatch):
    c, seen = _app(monkeypatch)
    r = c.post("/api/assistant/public", content=json.dumps({"question": "hi", "history": [{"q": "a", "a": "b"}]}),
               headers={"Origin": LANDING, "Content-Type": "text/plain"})
    assert r.status_code == 200, r.text
    assert r.headers["access-control-allow-origin"] == LANDING
    assert "live session" in r.text
    assert seen == [{"q": "hi", "guide": True, "history": [{"q": "a", "a": "b"}], "principal": "public"}]


def test_other_origins_are_refused_and_errors_still_carry_cors(monkeypatch):
    c, seen = _app(monkeypatch)
    assert c.post("/api/assistant/public", content='{"question":"hi"}', headers={"Origin": "https://evil.example"}).status_code == 403
    codes = [c.post("/api/assistant/public", content='{"question":"hi"}', headers={"Origin": LANDING}) for _ in range(4)]
    assert [r.status_code for r in codes] == [200, 200, 200, 429]
    assert codes[-1].headers["access-control-allow-origin"] == LANDING and "live session" in codes[-1].json()["detail"]
    assert len(seen) == 3


def test_long_questions_are_refused(monkeypatch):
    c, _ = _app(monkeypatch)
    assert c.post("/api/assistant/public", content=json.dumps({"question": "x" * 700})).status_code == 422
