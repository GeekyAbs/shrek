from datetime import datetime

import pytest

from shrek.bot import Bot
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
        "shrek.bot.parse",
        lambda text, key=None, model=None: ParsedEntry(
            amount=10.0, currency="INR", category="food",
            description="tea", confidence="llm",
        ),
    )
    bot = Bot(store, owner_id=1)
    _, eid = bot.owner_message("tea 10")
    assert store.get(eid).category_source == "llm"


def test_owner_message_stamps_regex_source(store, monkeypatch):
    monkeypatch.setattr(
        "shrek.bot.parse",
        lambda text, key=None, model=None: ParsedEntry(
            amount=10.0, currency="INR", category=None,
            description="tea", confidence="exact",
        ),
    )
    bot = Bot(store, owner_id=1)
    _, eid = bot.owner_message("tea 10")
    row = store.get(eid)
    assert row.category == UNCATEGORIZED
    assert row.category_source == "regex"


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
