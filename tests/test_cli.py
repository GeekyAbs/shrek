from datetime import datetime

import pytest
from typer.testing import CliRunner

from shrek.cli import app, swallow
from shrek.models import UNCATEGORIZED, Expense
from shrek.parser import ParsedEntry
from shrek.store import Store


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path / "ledger.db")


def mk(description, category=UNCATEGORIZED):
    return Expense(
        amount=100.0, currency="INR", category=category,
        description=description, source="cli", ts=datetime.now(),
    )


def test_swallow_stamps_regex_provenance(store):
    swallow("pizza 200 rupees", store, key=None)
    row = store.list()[0]
    assert row.category == UNCATEGORIZED
    assert row.category_source == "regex"


def test_swallow_stamps_llm_provenance(store, monkeypatch):
    monkeypatch.setattr(
        "shrek.ingest.llm.parse_expense",
        lambda text, key, model=None, default_currency="INR": ParsedEntry(
            amount=10.0, currency="INR", category="food",
            description="tea", confidence="llm",
        ),
    )
    swallow("tea 10 bucks", store, key="k")
    row = store.list()[0]
    assert row.category == "food"
    assert row.category_source == "llm"


def test_swallow_writes_multiple_rows(store):
    out = swallow("20 for rickshaw, 10 for bus", store, key=None)
    assert out.count("\n") == 1
    assert len(store.list()) == 2


def test_cli_currency_sets_and_shows(store, monkeypatch, tmp_path):
    from shrek import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")
    monkeypatch.delenv("SHREK_DEFAULT_CURRENCY", raising=False)
    result = CliRunner().invoke(app, ["currency", "usd"])
    assert result.exit_code == 0
    assert "USD" in result.output
    assert config.currency() == "USD"
    shown = CliRunner().invoke(app, ["currency"])
    assert "USD" in shown.output


def test_add_prompts_only_for_a_bare_amount(store, monkeypatch):
    calls = []
    monkeypatch.setattr("shrek.cli.default_store", lambda: store)
    monkeypatch.setattr("shrek.cli.current_key", lambda: None)
    monkeypatch.setattr("shrek.cli.config.model", lambda: "m")
    monkeypatch.setattr(
        "shrek.cli.config.ensure_currency",
        lambda: calls.append(1) or "INR",
    )
    CliRunner().invoke(app, ["add", "pizza 200 rupees"])
    assert calls == []
    CliRunner().invoke(app, ["add", "rickshaw 50"])
    assert calls == [1]


def test_cli_organize_dry_run_does_not_write(store, monkeypatch):
    eid = store.add(mk("bus ticket"))
    monkeypatch.setattr("shrek.cli.default_store", lambda: store)
    monkeypatch.setattr("shrek.cli.current_key", lambda: "k")
    monkeypatch.setattr("shrek.cli.config.model", lambda: "m")
    monkeypatch.setattr("shrek.llm.categorize", lambda rows, key, model=None: {eid: "transport"})
    result = CliRunner().invoke(app, ["organize", "--dry-run"])
    assert result.exit_code == 0
    assert "transport" in result.output
    assert store.get(eid).category == UNCATEGORIZED


def test_cli_organize_failure_exits_nonzero(store, monkeypatch):
    store.add(mk("bus ticket"))
    monkeypatch.setattr("shrek.cli.default_store", lambda: store)
    monkeypatch.setattr("shrek.cli.current_key", lambda: None)
    result = CliRunner().invoke(app, ["organize"])
    assert result.exit_code == 1


def test_cli_organize_applies_by_default(store, monkeypatch):
    eid = store.add(mk("bus ticket"))
    monkeypatch.setattr("shrek.cli.default_store", lambda: store)
    monkeypatch.setattr("shrek.cli.current_key", lambda: "k")
    monkeypatch.setattr("shrek.cli.config.model", lambda: "m")
    monkeypatch.setattr("shrek.llm.categorize", lambda rows, key, model=None: {eid: "transport"})
    result = CliRunner().invoke(app, ["organize"])
    assert result.exit_code == 0
    assert store.get(eid).category == "transport"
