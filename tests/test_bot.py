from datetime import datetime

import pytest

from shrek.bot import Bot
from shrek.ingest import ingest
from shrek.models import UNCATEGORIZED, Expense
from shrek.parser import ParsedEntry
from shrek.store import Store


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path / "bot.db")


def mk(description, category=UNCATEGORIZED):
    return Expense(
        amount=100.0, currency="INR", category=category,
        description=description, source="telegram", ts=datetime.now(),
    )


def test_owner_message_stamps_llm_source(store, monkeypatch):
    monkeypatch.setattr(
        "shrek.ingest.llm.parse_expense",
        lambda text, key, model=None, default_currency="INR": ParsedEntry(
            amount=10.0, currency="INR", category="food",
            description="tea", confidence="llm",
        ),
    )
    bot = Bot(store, owner_id=1)
    bot.owner_key = "k"
    _, ids = bot.owner_message("tea 10 bucks")
    assert store.get(ids[0]).category_source == "llm"


def test_owner_message_stamps_regex_source(store):
    bot = Bot(store, owner_id=1)
    bot.owner_key = None
    _, ids = bot.owner_message("pizza 200 rupees")
    row = store.get(ids[0])
    assert row.category == UNCATEGORIZED
    assert row.category_source == "regex"


def test_owner_message_splits_multiple_and_returns_every_id(store):
    bot = Bot(store, owner_id=1)
    bot.owner_key = None
    text, ids = bot.owner_message("20 for rickshaw, 10 for bus")
    assert len(ids) == 2
    assert len(store.list()) == 2
    assert text.count("\n") == 1


def test_owner_message_uses_the_env_currency(store, monkeypatch):
    monkeypatch.setattr("shrek.bot.config.currency", lambda: "AED")
    bot = Bot(store, owner_id=1)
    bot.owner_key = None
    _, ids = bot.owner_message("rickshaw 50")
    assert store.get(ids[0]).currency == "AED"


def test_set_currency_saves_and_normalizes(store, monkeypatch, tmp_path):
    from shrek import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")
    monkeypatch.delenv("SHREK_DEFAULT_CURRENCY", raising=False)
    bot = Bot(store, owner_id=1)
    msg = bot.set_currency("usd")
    assert "USD" in msg
    assert config.currency() == "USD"


def test_set_currency_rejects_a_bad_code(store):
    bot = Bot(store, owner_id=1)
    assert "three-letter" in bot.set_currency("dollars").lower()


def test_swap_category_stamps_manual(store):
    eid = store.add(mk("mystery"))
    bot = Bot(store, owner_id=1)
    bot.swap_category(1, eid, "bills")
    assert store.get(eid).category_source == "manual"


def test_organize_text_reports_and_caps_changes(store, monkeypatch):
    for i in range(25):
        store.add(mk(f"mystery {i}"))

    def fake_categorize(rows, key, model=None):
        return {row["id"]: "transport" for row in rows}

    monkeypatch.setattr("shrek.llm.categorize", fake_categorize)
    bot = Bot(store, owner_id=1)
    bot.owner_key = "k"
    bot.model = "m"
    out = bot.organize_text()
    assert "organized 25 of 25" in out
    assert "...and 5 more" in out
    assert store.list(category=UNCATEGORIZED) == []