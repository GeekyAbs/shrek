"""Two-layer parsing brain.

Layer 1 (this module's regex engine): deterministic parsing of simple
"X rupees" style lines — works offline, exhaustive-tested.
Layer 2 (shrek.llm): Gemini structured-JSON parse for messy lines.
"""

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ParsedEntry:
    """One parsed line, not yet committed to the ledger."""

    amount: float
    currency: str
    description: str = ""
    category: Optional[str] = None
    confidence: str = "exact"  # exact (regex) | llm | guess


#: Currency tokens -> ISO codes. Order matters beyond coverage: the
#: alternation regex below is built from this table.
CURRENCY_TOKENS: dict[str, str] = {
    "₹": "INR",
    "rs.": "INR",
    "rs": "INR",
    "rupay": "INR",
    "rupee": "INR",
    "rupees": "INR",
    "inr": "INR",
    "paise": "INR",
    "$": "USD",
    "dollar": "USD",
    "dollars": "USD",
    "usd": "USD",
    "€": "EUR",
    "euro": "EUR",
    "euros": "EUR",
    "eur": "EUR",
    "£": "GBP",
    "pound": "GBP",
    "pounds": "GBP",
    "gbp": "GBP",
}

_NUMBER = r"\d[\d,]*(?:\.\d+)?"
_CURRENCY_ALT = "|".join(re.escape(tok) for tok in CURRENCY_TOKENS)

# 200 rupees | 200₹ | ₹200 | rupees 200
# Lookbehind rejects digits/-/,/., so "-200 rupees" or "1,200" hidden inside
# another number cannot produce a phantom amount.
_LOOKBEHIND = r"(?<![\d.,\-])"
_CURRENCY_END = r"(?![A-Za-z])"  # ₹/non-word chars: \b is wrong at EOL
_PATTERN = re.compile(
    rf"{_LOOKBEHIND}({_NUMBER})\s*({_CURRENCY_ALT}){_CURRENCY_END}"
    rf"|{_LOOKBEHIND}({_CURRENCY_ALT})\.?\s*({_NUMBER})\b",
    re.IGNORECASE,
)


def _norm_currency(token: str) -> str:
    return CURRENCY_TOKENS[token.lower().strip()]


def _norm_amount(raw: str) -> float:
    """Strip separators ('2,50,000' and '12,50' both human-lakh/universal)."""
    cleaned = raw.replace(",", "")
    return float(cleaned)


def _extract_description(text: str, span: tuple[int, int]) -> str:
    """Everything outside the matched 'amount+currency' span."""
    a, b = span
    before = text[:a]
    after = text[b:]
    words = (before.strip() + " " + after.strip()).strip()
    return re.sub(r"\s+", " ", words).strip()


@dataclass
class _Hit:
    amount: float
    currency: str
    span: tuple[int, int]


def _first_hit(text: str) -> Optional[_Hit]:
    hits = []
    for m in _PATTERN.finditer(text):
        groups = m.groups()
        if groups[0] is not None:  # branch 1: amount then currency
            amount, cur = groups[0], groups[1]
            span = (m.start(1), m.end(2))
        else:  # branch 2: currency then amount
            cur, amount = groups[2], groups[3]
            span = (m.start(3), m.end(4))
        value = _norm_amount(amount)
        if value <= 0:
            continue
        hits.append(_Hit(amount=value, currency=_norm_currency(cur), span=span))
    if not hits:
        return None
    return hits[0]


def parse_regex(text: str) -> Optional[ParsedEntry]:
    """Parse 'X rupees'-style lines. Returns None when the line carries no
    (amount, currency) pair this engine can pin down — callers degrade
    gracefully; it never raises on bad input."""
    try:
        if not text or not text.strip():
            return None
        hit = _first_hit(text)
        if hit is None:
            return None
        return ParsedEntry(
            amount=hit.amount,
            currency=hit.currency,
            description=_extract_description(text, hit.span),
            confidence="exact",
        )
    except Exception:  # brief requirement: bad input never crashes
        return None


def parse(
    text: str,
    key: "str | None" = None,
    model: "str | None" = None,
) -> "ParsedEntry | None":
    """Two-layer brain: Gemini when a key exists (smart, messy lines),
    deterministic regex otherwise (offline, quota, no key). None on both —
    callers show a friendly 'I did not get that' message; nothing raises."""
    entry = None
    if key:
        from shrek import llm

        entry = llm.parse_expense(text, key=key, model=model or "gemini-2.5-flash")
    if entry is None:
        fallback = parse_regex(text)
        if fallback is not None and key:
            fallback.confidence = "fallback"
        entry = entry or fallback
    return entry
