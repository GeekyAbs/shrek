from datetime import datetime

import pytest

from shrek.models import UNCATEGORIZED, Expense
from shrek.organize import organize
from shrek.store import Store


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path / "ledger.db")


def mk(description, category=UNCATEGORIZED, amount=100.0):
    return Expense(
        amount=amount, currency="INR", category=category,
        description=description, source="cli", ts=datetime.now(),
    )


def test_no_candidates(store):
    store.add(mk("pizza", category="food"))
    rep = organize(store, key="k")
    assert rep.candidates == 0 and rep.changes == [] and rep.error is None


def test_no_key_is_an_error_and_writes_nothing(store):
    eid = store.add(mk("bus ticket"))
    rep = organize(store, key=None)
    assert rep.error and rep.applied is False
    assert store.get(eid).category == UNCATEGORIZED


def test_dry_run_proposes_without_writing(store, monkeypatch):
    eid = store.add(mk("bus ticket"))
    monkeypatch.setattr("shrek.llm.categorize", lambda rows, key, model=None: {eid: "transport"})
    rep = organize(store, key="k", dry_run=True)
    assert rep.applied is False
    assert [(c.old, c.new) for c in rep.changes] == [(UNCATEGORIZED, "transport")]
    assert store.get(eid).category == UNCATEGORIZED


def test_apply_writes_and_stamps_organized(store, monkeypatch):
    eid = store.add(mk("bus ticket"))
    monkeypatch.setattr("shrek.llm.categorize", lambda rows, key, model=None: {eid: "transport"})
    rep = organize(store, key="k")
    assert rep.applied is True
    row = store.get(eid)
    assert row.category == "transport" and row.category_source == "organized"


def test_returned_miscellaneous_is_a_noop(store, monkeypatch):
    eid = store.add(mk("mystery"))
    monkeypatch.setattr("shrek.llm.categorize", lambda rows, key, model=None: {eid: UNCATEGORIZED})
    rep = organize(store, key="k")
    assert rep.changes == []
    assert store.get(eid).category == UNCATEGORIZED


def test_invalid_category_is_filtered(store, monkeypatch):
    eid = store.add(mk("mystery"))
    monkeypatch.setattr("shrek.llm.categorize", lambda rows, key, model=None: {eid: "not-real"})
    rep = organize(store, key="k")
    assert rep.changes == []
    assert store.get(eid).category == UNCATEGORIZED


def test_categorizer_failure_writes_nothing(store, monkeypatch):
    eid = store.add(mk("bus ticket"))
    monkeypatch.setattr("shrek.llm.categorize", lambda rows, key, model=None: None)
    rep = organize(store, key="k")
    assert rep.error and rep.applied is False
    assert store.get(eid).category == UNCATEGORIZED
