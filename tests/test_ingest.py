import pytest

from shrek.ingest import ingest
from shrek.models import UNCATEGORIZED, Expense
from shrek.parser import ParsedEntry
from shrek.store import Store


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path / "ledger.db")


def test_regex_rows_exist_when_categorize_is_called(store, monkeypatch):
    seen = {}

    def fake_categorize(rows, key, model=None):
        seen["rows"] = sorted(row.description for row in store.list())
        return {row["id"]: "transport" for row in rows}

    monkeypatch.setattr("shrek.ingest.llm.categorize", fake_categorize)
    result = ingest("rickshaw 50, bus 10", store, key="k")
    assert seen["rows"] == ["bus", "rickshaw"]
    assert [(e.amount, e.category) for e in result.expenses] == [
        (50.0, "transport"), (10.0, "transport"),
    ]


def test_categorize_failure_leaves_rows_saved(store, monkeypatch):
    monkeypatch.setattr("shrek.ingest.llm.categorize", lambda rows, key, model=None: None)
    result = ingest("rickshaw 50", store, key="k")
    assert len(result.expenses) == 1
    assert store.get(result.expenses[0].id).category == UNCATEGORIZED
    assert store.get(result.expenses[0].id).category_source == "regex"


def test_categorize_exception_leaves_rows_saved(store, monkeypatch):
    def boom(rows, key, model=None):
        raise RuntimeError("network")

    monkeypatch.setattr("shrek.ingest.llm.categorize", boom)
    result = ingest("rickshaw 50", store, key="k")
    assert result.error is None
    assert store.get(result.expenses[0].id).amount == 50.0


def test_multiple_entries_become_separate_rows(store):
    result = ingest("20 for rickshaw, 10 for bus", store, key=None)
    assert [(e.amount, e.description) for e in result.expenses] == [
        (20.0, "rickshaw"), (10.0, "bus"),
    ]
    assert len(store.list()) == 2


def test_bare_amount_uses_the_given_default_currency(store):
    result = ingest("rickshaw 50", store, key=None, default_currency="AED")
    assert result.expenses[0].currency == "AED"


def test_refund_only_line_without_a_key_is_not_saved(store):
    result = ingest("got a refund 200 rupees", store, key=None)
    assert result.not_expense is True
    assert result.expenses == []
    assert store.list() == []


def test_llm_fallback_for_a_line_the_regex_cannot_parse(store, monkeypatch):
    monkeypatch.setattr(
        "shrek.ingest.llm.parse_expense",
        lambda text, key, model=None, default_currency="INR": ParsedEntry(
            amount=10.0, currency="INR", category="food",
            description="tea", confidence="llm",
        ),
    )
    result = ingest("tea 10 bucks", store, key="k")
    assert len(result.expenses) == 1
    assert store.get(result.expenses[0].id).category_source == "llm"


def test_llm_not_expense_is_not_saved(store, monkeypatch):
    from shrek import llm

    monkeypatch.setattr(
        "shrek.ingest.llm.parse_expense",
        lambda text, key, model=None, default_currency="INR": llm.NOT_EXPENSE,
    )
    result = ingest("refund 200", store, key="k")
    assert result.not_expense is True
    assert store.list() == []


def test_no_amount_and_no_key_is_an_error(store):
    result = ingest("hello there", store, key=None)
    assert result.error and result.expenses == []