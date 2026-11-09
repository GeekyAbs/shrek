"""Exhaustive pytest matrix for the deterministic regex expense parser."""

import pytest

from shrek.parser import ParsedEntry, parse_regex


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


def test_euro_symbols_and_words():
    assert parse_regex("paid 12.50 euros").currency == "EUR"
    assert parse_regex("€12.50 croissant").currency == "EUR"
    assert parse_regex("12,50 EUR metro").currency == "EUR"


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


# --- result shape ----------------------------------------------------------

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
