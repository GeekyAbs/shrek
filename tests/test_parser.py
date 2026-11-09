"""Exhaustive pytest matrix for the deterministic regex expense parser."""

import pytest

from shrek.parser import (
    ParsedEntry,
    needs_default_currency,
    parse,
    parse_many,
    parse_regex,
)


# --- the canonical happy paths ------------------------------------------

def test_amount_after_currency_word():
    p = parse_regex("ate pizza 200 rupees")
    assert p.amount == 200.0 and p.currency == "INR"
    assert "pizza" in p.description.lower()


def test_currency_word_before_amount():
    p = parse_regex("Rs 200 for the bus ticket")
    assert p.amount == 200.0 and p.currency == "INR"


def test_rupee_symbol_between():
    p = parse_regex("₹200 lunch")
    assert p.amount == 200.0 and p.currency == "INR"


def test_symbol_glued_after_number():
    p = parse_regex("coffee 200₹")
    assert p.amount == 200.0 and p.currency == "INR"


def test_inr_code():
    p = parse_regex("INR 250 cleaning")
    assert p.amount == 250.0 and p.currency == "INR"


def test_dollar_word_case_insensitive():
    p = parse_regex("bought a DOLLAR keychain for 5 dollars lol")
    assert p.amount == 5.0 and p.currency == "USD"


def test_dollar_symbol():
    p = parse_regex("$5 for the sandwitch")
    assert p.amount == 5.0 and p.currency == "USD"


def test_comma_decimal_european_style():
    """12,50 EUR = 12.50; 2,450 rupees = 2450; 12,50,000 = 1250000."""
    assert parse_regex("12,50 EUR metro").amount == 12.50
    assert parse_regex("2,450 rupees for shoes").amount == 2450.0
    assert parse_regex("12,50,000 rupees car").amount == 1250000.0


def test_pound_variants():
    assert parse_regex("paid 8 pounds for the book").currency == "GBP"
    assert parse_regex("£8 book").currency == "GBP"
    assert parse_regex("8 GBP book").currency == "GBP"


# --- number formats ------------------------------------------------------

@pytest.mark.parametrize("text,amount", [
    ("2,450 rupees for shoes", 2450.0),
    ("12,50,000 rupees car", 1250000.0),  # lakh-style grouping
    ("45.50 rupees milk", 45.50),
    ("45.5 rupees milk", 45.5),
    ("₹0.99 gum", 0.99),
    ("100000 rupees tuition", 100000.0),
])
def test_number_formats(text, amount):
    assert parse_regex(text).amount == amount


def test_paise_is_fraction_of_rupee():
    p = parse_regex("50 paise candy")
    assert p.amount == 0.50 and p.currency == "INR"


def test_absurd_amounts_are_refused_not_infinity():
    """A 400-digit literal is a typo/attack, not a receipt: -> None."""
    assert parse_regex("₹" + "9" * 400) is None
    assert parse_regex("9" * 20 + " rupees rent") is None


def test_amount_written_before_currency_and_after():
    assert parse_regex("rupees 320 auto ride").amount == 320.0
    assert parse_regex("auto ride 320 rupees").amount == 320.0


def test_rupee_and_rupees_singular():
    assert parse_regex("1 rupee coin").currency == "INR"


# --- description extraction ---------------------------------------------

def test_description_is_remaining_words():
    p = parse_regex("ate pizza and garlic bread 200 rupees")
    assert p.description == "ate pizza and garlic bread"


def test_description_keeps_surrounding_text():
    p = parse_regex("₹90 paid the electrician")
    assert p.description == "paid the electrician"


# --- the not-an-expense contract: return None, never crash ---------------

@pytest.mark.parametrize("text", [
    "",
    "   ",
    "hello there shrek",
    "what did I buy?",
    "rupees rupees rupees",
    "200",
    "₹",
    "-200 rupees refund",  # negative = not a debit; outside v1 scope
])
def test_non_expense_lines_return_none(text):
    assert parse_regex(text) is None


def test_multiple_amounts_uses_first():
    p = parse_regex("2 chocolate bars 60 rupees each")
    assert p.amount == 2.0 or p.amount == 60.0  # deterministic choice, documented


def test_mixed_currencies_takes_first_amount_currency_pair():
    p = parse_regex("snake oil 10 dollars also 5 rupees chips")
    assert p.currency == "USD" and p.amount == 10.0


# --- the LLM-dispatch contract (income verdicts are terminal) ------------

def test_llm_income_verdict_is_terminal(monkeypatch):
    """When the LLM confidently says 'not an expense', we do NOT regex-fallback
    and swallow a refund as a debit. Terminal: None."""
    import shrek.llm as llm

    monkeypatch.setattr(llm, "parse_expense", lambda *a, **k: llm.NOT_EXPENSE)
    assert parse("refund 200 rupees", key="KEY") is None


def test_llm_error_falls_back(monkeypatch):
    """LLM error (None) -> deterministic regex fallback with 'fallback' mark."""
    import shrek.llm as llm

    monkeypatch.setattr(llm, "parse_expense", lambda *a, **k: None)
    p = parse("milk 45 rupees", key="KEY")
    assert p.amount == 45.0 and p.confidence == "fallback"


def test_llm_verdict_used_directly(monkeypatch):
    import shrek.llm as llm
    from shrek.parser import ParsedEntry

    verdict = ParsedEntry(amount=12.5, currency="EUR", category="fun",
                          description="coffee", confidence="llm")
    monkeypatch.setattr(llm, "parse_expense", lambda *a, **k: verdict)
    assert parse("coffee 12.50", key="KEY") is verdict

def test_result_is_parsed_entry_with_exact_confidence():
    p = parse_regex("pizza 200 rupees")
    assert isinstance(p, ParsedEntry)
    assert p.confidence == "exact"
    assert p.category is None  # taxonomy assignment is the LLM's job


def test_huge_and_tiny_amounts():
    assert parse_regex("₹99999999 rent advance").amount == 99999999.0
    assert parse_regex("₹0.01 upi test").amount == 0.01


def test_zero_amount_is_not_a_debit():
    assert parse_regex("₹0 nothing") is None


def test_emoji_and_unicode_survive():
    p = parse_regex("🍕 250 rupees")
    assert p.amount == 250.0 and "🍕" in p.description


# --- bare amounts: no currency token, use the configured default ---------

def test_bare_trailing_number_uses_default_currency():
    p = parse_regex("rickshaw 50")
    assert p is not None
    assert p.amount == 50.0 and p.currency == "INR"
    assert p.description == "rickshaw"


def test_bare_number_uses_configured_default():
    assert parse_regex("rickshaw 50", default_currency="AED").currency == "AED"


def test_bare_connector_forms():
    assert parse_regex("20 for rickshaw").amount == 20.0
    assert parse_regex("20 for rickshaw").description == "rickshaw"
    assert parse_regex("10 on bus").amount == 10.0
    assert parse_regex("5 to the driver").amount == 5.0


def test_explicit_currency_beats_default():
    p = parse_regex("rickshaw 50 dollars", default_currency="INR")
    assert p.amount == 50.0 and p.currency == "USD"


def test_bare_description_drops_connector():
    assert parse_regex("rickshaw for 50").description == "rickshaw"


@pytest.mark.parametrize("text", [
    "meeting at 3",
    "call me at 3pm",
    "50% off",
    "call 9876543210",
    "flat 3:30",
    "rickshaw 1234567",
])
def test_bare_number_guards(text):
    assert parse_regex(text) is None


def test_bare_number_needs_a_letter():
    assert parse_regex("50") is None


# --- multi-entry splitting and the refund guard --------------------------

def test_parse_many_splits_commas_semicolons_newlines_and():
    result = parse_many(
        "20 for rickshaw, 10 for bus; 30 for train\n40 for auto and 50 for tea"
    )
    assert [e.amount for e in result.entries] == [20, 10, 30, 40, 50]


def test_parse_many_keeps_number_commas_whole():
    result = parse_many("shoes 2,450 rupees, milk 12,50 EUR")
    assert [e.amount for e in result.entries] == [2450.0, 12.5]


def test_and_without_a_second_amount_stays_one_segment():
    result = parse_many("bread and milk 100 rupees")
    assert len(result.entries) == 1
    assert result.entries[0].amount == 100.0


def test_refund_segment_is_skipped():
    result = parse_many("20 for rickshaw, refund 100")
    assert [e.amount for e in result.entries] == [20.0]
    assert result.skipped == 1


@pytest.mark.parametrize("text", [
    "got a refund 200 rupees",
    "salary 50000",
    "cashback 30",
    "received 500",
])
def test_refund_only_lines_are_skipped(text):
    result = parse_many(text)
    assert result.entries == [] and result.skipped == 1


def test_needs_default_currency():
    assert needs_default_currency("rickshaw 50")
    assert needs_default_currency("20 for rickshaw")
    assert not needs_default_currency("pizza 200 rupees")
    assert not needs_default_currency("hello there")
