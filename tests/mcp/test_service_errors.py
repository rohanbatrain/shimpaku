"""Defect 7 (docs/INCIDENTS.md): a failed action stores a plain error string, but every typed
caller (MCP ActionResult) expects `error` to be a {code, message} dict."""

from shimpaku import service
from shimpaku.mcp.server import ActionResult


def test_failed_action_error_string_is_normalised():
    a = {"id": "act-x", "status": "failed", "error": "G1 re-check at execution failed: live role trunk"}
    out = service._normalise_action_error(a)
    assert out["error"] == {"code": "execution_failed", "message": a["error"]}
    ActionResult(**out)  # the typed MCP model accepts it (it raised before the fix)


def test_structured_or_empty_error_untouched():
    for err in (None, {"code": "not_found", "message": "x"}):
        assert service._normalise_action_error({"id": "a", "error": err})["error"] == err
