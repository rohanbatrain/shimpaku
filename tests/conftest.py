import pytest


@pytest.fixture(autouse=True)
def _privacy_contact(monkeypatch):
    """Session-request intake needs a privacy contact (DPDP notice); tests that check the closed
    state clear it explicitly."""
    from shimpaku.config import settings
    monkeypatch.setattr(settings, "privacy_contact", "privacy-test@invalid")


@pytest.fixture(autouse=True)
def _isolated_data_dir(monkeypatch, tmp_path_factory):
    """No test reads the developer's real data dir (admin.hash, totp.json)."""
    from shimpaku.config import settings
    monkeypatch.setattr(settings, "data", tmp_path_factory.mktemp("shimpaku-data"))
