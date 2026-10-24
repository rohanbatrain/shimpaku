from typer.testing import CliRunner

from shimpaku.assistant import cli
from shimpaku.assistant.fixture import SCENARIOS, MockLab
from shimpaku.assistant.llm import LLM
from shimpaku.assistant.loop import Assistant

from .scripted import ScriptedModel, mock_gateway


def test_ask_cli_streams_answer_and_trace(monkeypatch):
    sc = next(s for s in SCENARIOS if s["id"].startswith("S07"))
    lab = MockLab(sc)
    monkeypatch.setattr(cli, "Assistant", lambda _url, **kw: Assistant(
        lab.server, **kw, llm_factory=lambda n: LLM(n, key="k")))
    with mock_gateway(ScriptedModel()):
        r = CliRunner().invoke(cli.app, [sc["prompt"], "--model", "diagnose"])
    assert r.exit_code == 0, r.output
    assert "Likely root cause:" in r.stdout
    assert "sle_get_summary" in r.stderr
