from shrek.models import CATEGORIES, UNCATEGORIZED, Expense, coerce_category


def test_taxonomy_has_eleven_buckets():
    assert len(CATEGORIES) == 11
    assert CATEGORIES[-2:] == ("other", "miscellaneous")


def test_unknown_and_none_coerce_to_miscellaneous():
    assert UNCATEGORIZED == "miscellaneous"
    assert coerce_category(None) == UNCATEGORIZED
    assert coerce_category("yetti-snacks") == UNCATEGORIZED
    assert coerce_category("Food") == "food"
    assert coerce_category("other") == "other"


def test_expense_defaults_to_miscellaneous_and_unknown_source():
    e = Expense(amount=10.0)
    assert e.category == UNCATEGORIZED
    assert e.category_source == "unknown"
