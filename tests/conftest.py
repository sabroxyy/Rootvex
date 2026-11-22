import pytest

from rootvex import log


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """Keep tests independent of the developer's .env and real log dir."""
    for var in list(__import__("os").environ):
        if var.startswith("ROOTVEX_"):
            monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ROOTVEX_LOGGING__DIR", str(tmp_path / "logs"))
    # Never load the developer's real .env (lab passwords) into tests.
    monkeypatch.setenv("ROOTVEX_ENV_FILE", str(tmp_path / "no.env"))
    log._known_secrets.clear()
    yield
