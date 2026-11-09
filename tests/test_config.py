import os

import pytest

from shrek import config


@pytest.fixture(autouse=True)
def _restore_env():
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")
    monkeypatch.delenv("SHREK_DEFAULT_CURRENCY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    return tmp_path


def test_currency_is_unset_on_a_fresh_config(cfg):
    assert config.currency() is None


def test_save_currency_persists_and_normalizes(cfg):
    config.save_currency("usd")
    assert config.currency() == "USD"
    assert "SHREK_DEFAULT_CURRENCY=USD" in (cfg / ".env").read_text()


def test_real_environment_wins_over_the_file(cfg, monkeypatch):
    config.save_currency("USD")
    monkeypatch.setenv("SHREK_DEFAULT_CURRENCY", "EUR")
    assert config.currency() == "EUR"


def test_ensure_currency_headless_falls_back_to_inr(cfg, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert config.ensure_currency() == "INR"
    assert "INR" in capsys.readouterr().err


def test_ensure_currency_prompts_and_saves(cfg, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "3")
    assert config.ensure_currency() == "EUR"
    assert config.currency() == "EUR"


def test_ensure_currency_enter_uses_inr(cfg, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    assert config.ensure_currency() == "INR"
    assert config.currency() == "INR"


def test_ensure_currency_ctrl_c_falls_back(cfg, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    def boom(prompt=""):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", boom)
    assert config.ensure_currency() == "INR"


def test_wizard_asks_for_the_currency(cfg, monkeypatch):
    from shrek import llm

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(config, "getpass", lambda prompt="": "key")
    monkeypatch.setattr(llm, "list_models", lambda key: ["m1", "m2"])
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    config.wizard()
    assert config.currency() == "INR"


def test_save_still_accepts_key_and_model(cfg):
    config.save("key", "model")
    assert config.gemini_key() == "key"
