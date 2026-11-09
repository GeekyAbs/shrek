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
        "shrek.cli.parse",
        lambda text, key=None, model=None: ParsedEntry(
            amount=10.0, currency="INR", category="food",
            description="tea", confidence="llm",
        ),
    )
    swallow("tea 10 bucks", store, key="k")
    row = store.list()[0]
    assert row.category == "food"
    assert row.category_source == "llm"


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


def test_cli_organize_applies_by_default(store, monkeypatch):
    eid = store.add(mk("bus ticket"))
    monkeypatch.setattr("shrek.cli.default_store", lambda: store)
    monkeypatch.setattr("shrek.cli.current_key", lambda: "k")
    monkeypatch.setattr("shrek.cli.config.model", lambda: "m")
    monkeypatch.setattr("shrek.llm.categorize", lambda rows, key, model=None: {eid: "transport"})
    result = CliRunner().invoke(app, ["organize"])
    assert result.exit_code == 0
    assert store.get(eid).category == "transport"
