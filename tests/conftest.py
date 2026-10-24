import pytest


@pytest.fixture(autouse=True)
def _privacy_contact(monkeypatch):
    """Session-request intake needs a privacy contact (DPDP notice); tests that check the closed
    state clear it explicitly."""
    from shimpaku.config import settings
    monkeypatch.setattr(settings, "privacy_contact", "privacy-test@invalid")
