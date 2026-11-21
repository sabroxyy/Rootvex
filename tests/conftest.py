import pytest

from netwarden import log


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """Keep tests independent of the developer's .env and real log dir."""
    for var in list(__import__("os").environ):
        if var.startswith("NETWARDEN_"):
            monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NETWARDEN_LOGGING__DIR", str(tmp_path / "logs"))
    log._known_secrets.clear()
    yield
