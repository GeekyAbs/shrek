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


def _norm_amount(raw: str, scale: float = 1.0) -> float:
    """Commas are continent-dependent, so: no '.' + one comma + exactly two
    trailing digits => decimal comma ('12,50' = 12.50); otherwise strip
    thousands separators ('2,450', '12,50,000' are groupings). `scale`
    handles sub-units (paise = 1/100 rupee)."""
    cleaned = raw.replace(",", "")
    if "." not in raw:
        end = raw.rsplit(",", 1)[-1]
        if raw.count(",") == 1 and len(end) == 2:
            cleaned = raw.replace(",", ".")
    value = float(cleaned) * scale
    if value > 1e13:  # 400-digit literal is a typo/attack, not a receipt
        raise ValueError("absurd amount")
    return value


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
        value = _norm_amount(amount, scale=0.01 if cur.lower() == "paise" else 1.0)
        if value <= 0:
            continue
        # sub-unit tokens collapse into the base currency (paise -> INR)
        currency_code = "INR" if cur.lower() == "paise" else _norm_currency(cur)
        hits.append(_Hit(amount=value, currency=currency_code, span=span))
    if not hits:
        return None
    return hits[0]


#: A bare number: no explicit currency token, so it only counts when it sits
#: in a spend-shaped position (see `_bare_hit`).
_BARE = re.compile(rf"(?<![\d.,\-:])({_NUMBER})")
#: A number right after one of these words is a clock time, not a spend.
_TIME_BEFORE = re.compile(
    r"\b(at|around|by|till|until|before|after|past)\s*$", re.IGNORECASE
)
#: Clock/percent/size shapes: `3pm`, `3 am`, `50%`, `2x`.
_NOT_MONEY_AFTER = re.compile(r"\s*(?:%|[xX]\b|(?:am|pm)\b)", re.IGNORECASE)
#: `20 for rickshaw`, `10 on bus`, `5 to the driver`.
_CONNECTOR_AFTER = re.compile(r"\s+(?:for|on|to|of)\b\s+\S", re.IGNORECASE)
_CONNECTOR_EDGE = re.compile(r"^(?:for|on|to|of)\b\s*|\s*\b(?:for|on|to|of)$",
                             re.IGNORECASE)


def _clean_description(text: str, span: tuple[int, int]) -> str:
    """Description for a bare-amount hit: drop the connector that made the
    number spend-shaped (`20 for rickshaw` -> 'rickshaw', `rickshaw for 50`
    -> 'rickshaw')."""
    desc = _extract_description(text, span)
    desc = desc.strip(" .,;:-!?")
    desc = _CONNECTOR_EDGE.sub("", desc).strip()
    return re.sub(r"\s+", " ", desc).strip()


def _bare_hit(text: str) -> Optional[_Hit]:
    """Find a number with no currency token that is plausibly a spend:
    trailing (`rickshaw 50`) or followed by `for|on|to|of` (`20 for lunch`).
    Context guards reject times, phone numbers, IDs, percentages and sizes."""
    for m in _BARE.finditer(text):
        raw = m.group(1)
        integer = raw.split(".", 1)[0].replace(",", "")
        if len(integer) > 6 or not integer:
            continue
        try:
            value = _norm_amount(raw)
        except ValueError:
            continue
        if value <= 0:
            continue
        before = text[: m.start()]
        after = text[m.end():]
        if _TIME_BEFORE.search(before):
            continue
        if _NOT_MONEY_AFTER.match(after):
            continue
        trailing = re.fullmatch(r"[.!?]*\s*", after) is not None
        connected = _CONNECTOR_AFTER.match(after) is not None
        if not (trailing or connected):
            continue
        if not any(ch.isalpha() for ch in before + after):
            continue
        return _Hit(amount=value, currency="", span=(m.start(1), m.end(1)))
    return None


def parse_regex(
    text: str, default_currency: str = "INR"
) -> Optional[ParsedEntry]:
    """Parse 'X rupees'-style lines. Returns None when the line carries no
    (amount, currency) pair this engine can pin down — callers degrade
    gracefully; it never raises on bad input.

    A bare amount (`rickshaw 50`, `20 for rickshaw`) uses `default_currency`;
    an explicit token always wins."""
    try:
        if not text or not text.strip():
            return None
        hit = _first_hit(text)
        if hit is not None:
            return ParsedEntry(
                amount=hit.amount,
                currency=hit.currency,
                description=_extract_description(text, hit.span),
                confidence="exact",
            )
        currency = (default_currency or "").strip().upper()
        if not currency:
            return None
        bare = _bare_hit(text)
        if bare is None:
            return None
        return ParsedEntry(
            amount=bare.amount,
            currency=currency,
            description=_clean_description(text, bare.span),
            confidence="exact",
        )
    except Exception:  # brief requirement: bad input never crashes
        return None


#: Segments with these words are income/refund, never a debit the regex writes.
INCOME_WORDS = re.compile(
    r"\b(refund(?:ed)?|received|credited|cashback|salary|income(?!\s+tax)|reimbursed?)\b",
    re.IGNORECASE,
)
#: Top-level separators: a comma between digits is never a split (`2,450`).
_SEGMENT_SPLIT = re.compile(r"[;\n]+|,(?!\d)")
_AND_SPLIT = re.compile(r"\s+\band\b\s+", re.IGNORECASE)


@dataclass
class ManyResult:
    """Every entry one message produced, plus how many refund-ish segments
    the regex deliberately left for the LLM."""

    entries: list[ParsedEntry] = field(default_factory=list)
    skipped: int = 0


def _split_and(piece: str, default_currency: str) -> list[str]:
    """Split on `and` only when both sides carry an amount of their own."""
    for m in _AND_SPLIT.finditer(piece):
        left, right = piece[: m.start()], piece[m.end():]
        if (parse_regex(left, default_currency) is not None
                and parse_regex(right, default_currency) is not None):
            return (
                _split_and(left, default_currency)
                + _split_and(right, default_currency)
            )
    return [piece]


def _split_segments(text: str, default_currency: str) -> list[str]:
    pieces: list[str] = []
    for raw in _SEGMENT_SPLIT.split(text):
        if raw.strip():
            pieces.extend(_split_and(raw, default_currency))
    return [p.strip() for p in pieces if p.strip()]


def parse_many(
    text: str, default_currency: str = "INR"
) -> ManyResult:
    """Split a message into expenses. Refund/income segments are skipped (the
    LLM may still judge them); bad input yields whatever was collected."""
    result = ManyResult()
    try:
        if not text or not text.strip():
            return result
        for segment in _split_segments(text, default_currency):
            if INCOME_WORDS.search(segment):
                result.skipped += 1
                continue
            entry = parse_regex(segment, default_currency)
            if entry is not None:
                result.entries.append(entry)
        return result
    except Exception:  # brief requirement: bad input never crashes
        return result


def needs_default_currency(text: str) -> bool:
    """True when the line has a bare amount and no explicit currency token,
    i.e. the CLI should ask for (or apply) a default currency."""
    try:
        if not text or not text.strip():
            return False
        if _first_hit(text) is not None:
            return False
        if _bare_hit(text) is not None:
            return True
        return any(
            _bare_hit(piece) is not None
            for piece in _SEGMENT_SPLIT.split(text)
            if piece.strip()
        )
    except Exception:
        return False


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

        entry = llm.parse_expense(text, key=key, model=model)
        if entry is llm.NOT_EXPENSE:
            return None  # terminal: the smart brain judged; do not regex-overrule it
    if entry is None:
        fallback = parse_regex(text)
        if fallback is not None and key:
            fallback.confidence = "fallback"
        entry = entry or fallback
    return entry
