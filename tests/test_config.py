import ipaddress

from rootvex.config import load_settings


def test_defaults_come_from_settings_yaml():
    s = load_settings()
    assert s.llm.model == "granite4:3b"
    assert s.agent.max_steps == 12
    assert ipaddress.IPv4Network("10.99.0.0/24") in s.lab.allowed_networks


def test_env_overrides_yaml(monkeypatch):
    monkeypatch.setenv("ROOTVEX_LLM__MODEL", "qwen3.5:4b")
    monkeypatch.setenv("ROOTVEX_AGENT__MAX_STEPS", "3")
    s = load_settings()
    assert s.llm.model == "qwen3.5:4b"
    assert s.agent.max_steps == 3


def test_api_key_is_secret():
    s = load_settings()
    assert "ollama" not in repr(s.llm)


def test_llm_limits_reach_the_client(monkeypatch):
    from rootvex.llm import build_llm

    monkeypatch.setenv("ROOTVEX_LLM__REASONING_EFFORT", "none")
    s = load_settings()
    llm = build_llm(s.llm)
    assert llm.max_tokens == 2048
    assert llm.reasoning_effort == "none"


def test_dotenv_values_reach_the_environment(tmp_path, monkeypatch):
    from rootvex.config import export_dotenv

    monkeypatch.delenv("LAB_TEST_PASSWORD", raising=False)
    monkeypatch.setenv("LAB_ALREADY_SET", "from-shell")
    env = tmp_path / ".env"
    env.write_text(
        "# comment\nLAB_TEST_PASSWORD='Lab-R0uter'\nLAB_ALREADY_SET=from-file\nexport LAB_X=1\n"
    )
    added = export_dotenv(env)
    import os

    assert os.environ["LAB_TEST_PASSWORD"] == "Lab-R0uter"
    assert os.environ["LAB_ALREADY_SET"] == "from-shell"  # real env wins
    assert "LAB_TEST_PASSWORD" in added and "LAB_ALREADY_SET" not in added
    monkeypatch.delenv("LAB_TEST_PASSWORD")
    monkeypatch.delenv("LAB_X")
