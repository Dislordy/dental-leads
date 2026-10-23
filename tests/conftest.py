import pytest

from leadgen import config


@pytest.fixture(autouse=True)
def _ignore_local_settings(tmp_path, monkeypatch):
    """Keep tests independent of any settings.local.yaml / .env saved on this machine."""
    monkeypatch.setattr(config, "LOCAL_SETTINGS", tmp_path / "settings.local.yaml")
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")
