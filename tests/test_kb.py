from shimpaku import kb


def test_index_covers_the_engineering_docs_and_only_the_allow_list():
    paths = {c.path for c in kb.index().chunks}
    assert {"README.md", "SECURITY.md", "docs/build/mcp-actions.md"} <= paths
    assert any(p.startswith("src/shimpaku/") for p in paths)
    assert not any(p.startswith((".local", ".venv", "web/node_modules")) for p in paths)


def test_search_finds_the_guardrails_and_read_returns_the_section():
    r = kb.search("guardrails bounce cap port", 5)
    assert r["results"] and any("guardrail" in (x["section"] + x["text"]).lower() for x in r["results"])
    top = r["results"][0]
    doc = kb.read(top["path"])
    assert top["path"] == doc["path"] and doc["text"]
    assert "error" in kb.read("../.local/settings.env")


def test_scrub_removes_addresses_and_key_like_strings():
    s = kb.scrub("mgmt 10.99.0.5 and key sk-abcdef1234567890 and fe80::1")
    assert "10.99.0.5" not in s and "sk-abcdef" not in s and "<host>" in s and "<redacted>" in s
