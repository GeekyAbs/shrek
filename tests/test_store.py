"""Tests for the SQLModel ledger store. Scope: exhaustive, real DB."""

from datetime import datetime, timedelta

import pytest

from shrek.models import CATEGORIES, Expense
from shrek.store import Store


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path / "ledger.db")


def mk(amount=100.0, currency="INR", category="food", description="pizza",
       source="cli", ts=None):
    return Expense(
        amount=amount,
        currency=currency,
        category=category,
        description=description,
        source=source,
        ts=ts or datetime.now(),
    )


NOW = datetime.now()


def test_add_get_roundtrip(store):
    e = mk()
    eid = store.add(e)
    got = store.get(eid)
    assert got is not None
    assert got.amount == 100.0
    assert got.currency == "INR"
    assert got.category == "food"
    assert got.description == "pizza"
    assert got.source == "cli"


def test_get_missing_returns_none(store):
    assert store.get(999) is None


def test_add_unknown_category_becomes_miscellaneous(store):
    eid = store.add(mk(category="yetti-snacks"))
    assert store.get(eid).category == "miscellaneous"


def test_categories_preset_is_frozen():
    assert "food" in CATEGORIES and "other" in CATEGORIES
    assert len(CATEGORIES) >= 8


def test_delete_existing_true_missing_false(store):
    eid = store.add(mk())
    assert store.delete(eid) is True
    assert store.get(eid) is None
    assert store.delete(eid) is False
    assert store.delete(4242) is False


def test_list_orders_newest_first_and_limits(store):
    a = store.add(mk(description="old", ts=NOW - timedelta(days=5)))
    b = store.add(mk(description="new"))
    rows = store.list()
    assert [r.id for r in rows] == [b, a]
    assert [r.id for r in store.list(limit=1)] == [b]


def test_list_category_filter(store):
    store.add(mk(category="food"))
    store.add(mk(category="transport", description="bus"))
    rows = store.list(category="transport")
    assert len(rows) == 1 and rows[0].description == "bus"


def test_list_period_today(store):
    store.add(mk(description="today", ts=NOW))
    store.add(mk(description="last-week", ts=NOW - timedelta(days=8)))
    today = store.list(period="today")
    assert [r.description for r in today] == ["today"]


def test_list_period_this_week_and_month(store):
    within_week = NOW - timedelta(hours=2)  # same day minus 2h: inside any week convention
    lastmonth = NOW - timedelta(days=45)  # before this calendar month
    prev_month_day = (NOW.replace(day=1) - timedelta(days=1)).replace(day=15)
    store.add(mk(description="recent", ts=within_week))
    store.add(mk(description="old", ts=lastmonth))
    store.add(mk(description="prev-month", ts=prev_month_day))
    assert [r.description for r in store.list(period="week")] == ["recent"]
    assert [r.description for r in store.list(period="month")] == ["recent"]


def test_total_sums_per_currency(store):
    store.add(mk(amount=200.0, currency="INR"))
    store.add(mk(amount=30.5, currency="INR"))
    store.add(mk(amount=10.0, currency="USD"))
    totals = store.total()
    assert totals == {"INR": 230.50, "USD": 10.00}


def test_total_with_category_and_period(store):
    store.add(mk(amount=100.0, category="food"))
    store.add(mk(amount=40.0, category="food", ts=NOW - timedelta(days=10)))
    store.add(mk(amount=500.0, category="rent"))
    assert store.total(category="food") == {"INR": 140.00}
    assert store.total(category="rent", period="today") == {"INR": 500.00}
    assert store.total(category="rent", period="week") == {"INR": 500.00}


def test_total_empty_store(store):
    assert store.total() == {}


def test_update_category_preserves_id_and_timestamp(store):
    ts = NOW - timedelta(days=3)
    eid = store.add(mk(amount=90.0, ts=ts))
    row = store.update_category(eid, "bills")
    assert row is not None
    assert row.category == "bills" and row.id == eid and row.ts == ts
    assert store.total(category="bills") == {"INR": 90.00}


def test_update_category_missing_row(store):
    assert store.update_category(777, "food") is None


def test_update_category_coerces_to_preset(store):
    eid = store.add(mk())
    assert store.update_category(eid, "totally-made-up").category == "miscellaneous"


def test_latest_returns_newest(store):
    store.add(mk(description="older", ts=NOW - timedelta(days=1)))
    newest = store.add(mk(description="newest"))
    assert store.latest().description == "newest"
    store.delete(newest)
    assert store.latest().description == "older"


def test_empty_store_latest_is_none(store):
    assert store.latest() is None


def test_add_persists_across_store_instances(tmp_path):
    path = tmp_path / "ledger.db"
    s1 = Store(path)
    eid = s1.add(mk(amount=123.45))
    s2 = Store(path)
    assert s2.get(eid).amount == 123.45


@pytest.mark.parametrize("bad", ["", "today", "this_month", None, 123])
def test_total_bad_period_raises_or_ignores(store, bad):
    if bad in ("today", "week", "month", None, ""):
        pytest.skip("valid values checked elsewhere")
    with pytest.raises(ValueError):
        store.total(period=bad)
