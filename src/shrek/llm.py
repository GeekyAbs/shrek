"""Gemini structured-JSON parsing of expense lines (the smart swallow)."""

import json
import sys
import typing as t

from shrek.config import DEFAULT_MODEL
from shrek.models import CATEGORIES, coerce_category
from shrek.parser import ParsedEntry

#: Structured output contract sent to Gemini.
_EXPENSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "type": {"type": "STRING", "enum": ["expense", "income", "not_expense"]},
        "amount": {"type": "NUMBER"},
        "currency": {"type": "STRING"},
        "category": {"type": "STRING", "enum": list(CATEGORIES)},
        "description": {"type": "STRING"},
    },
    "required": ["type", "amount", "currency", "category", "description"],
}

_PROMPT = (
    "You are shrek, an ogre who swallows expenses and organizes a ledger. "
    "Read one line written by a human about money they SPENT (a debit). "
    "Extract: amount (positive number), currency (ISO code like INR/USD/EUR/GBP, "
    "default INR for rupees), category picked ONLY from the allowed enum, and a "
    "short cleaned description. If the line does not describe a spending "
    "(a refund, income, or none sense), set type to income or not_expense and "
    "leave amount 0."
)


def _prompt(default_currency: str) -> str:
    """The parse prompt with the user's configured fallback currency."""
    code = (default_currency or "INR").strip().upper() or "INR"
    return (
        f"{_PROMPT} The default currency is {code} when the line does not "
        "name one."
    )

#: Structured output contract for the batched organize call.
_CATEGORIZE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "id": {"type": "INTEGER"},
            "category": {"type": "STRING", "enum": list(CATEGORIES)},
        },
        "required": ["id", "category"],
    },
}

_CATEGORIZE_PROMPT = (
    "You organize a personal expense ledger. For every row you receive "
    "(id, description, amount, currency), pick the single best category "
    "from the allowed enum. Use 'miscellaneous' only when the description "
    "does not let you decide. Return one {id, category} object per input "
    "id, and never invent ids."
)

#: Sentinel meaning "the LLM confidently judged this line is not a spend"
#: (income, refund, chit-chat, praise for ogres). Callers MUST treat it as
#: terminal — no regex fallback — so a refund is never swallowed as a debit.
NOT_EXPENSE = object()


def parse_expense(
    text: str,
    key: str,
    model: str = DEFAULT_MODEL,
    default_currency: str = "INR",
) -> t.Optional[ParsedEntry]:
    """LLM-parse one line. Never raises; returns None on any problem so the
    caller can fall back to the deterministic regex engine."""
    if not key or not text or not text.strip():
        return None
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=key)
        response = client.models.generate_content(
            model=model,
            contents=[text],
            config=types.GenerateContentConfig(
                system_instruction=_prompt(default_currency),
                response_mime_type="application/json",
                response_schema=_EXPENSE_SCHEMA,
                temperature=0.1,
            ),
        )
        raw = response.text
        if not raw:
            return None
        data = json.loads(raw)
        if data.get("type") != "expense":  # income/non-expense: terminal verdict
            return NOT_EXPENSE
        amount = float(data.get("amount") or 0)
        if amount <= 0:
            return None
        return ParsedEntry(
            amount=amount,
            currency=str(data.get("currency") or default_currency).upper() or default_currency.upper(),
            category=coerce_category(data.get("category")),
            description=str(data.get("description") or "").strip(),
            confidence="llm",
        )
    except Exception as exc:  # brief: bad input/keys never crash the tool
        print(f"shrek: gemini parse failed ({type(exc).__name__}); using fallback", file=sys.stderr)
        return None


def categorize(
    rows: list[dict],
    key: str,
    model: str = DEFAULT_MODEL,
) -> t.Optional[dict[int, str]]:
    """Categorize every row in one Gemini call. Never raises; returns None
    on any problem so the caller can leave the ledger untouched."""
    if not key or not rows:
        return None
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=key)
        response = client.models.generate_content(
            model=model,
            contents=[json.dumps(rows)],
            config=types.GenerateContentConfig(
                system_instruction=_CATEGORIZE_PROMPT,
                response_mime_type="application/json",
                response_schema=_CATEGORIZE_SCHEMA,
                temperature=0.1,
            ),
        )
        raw = response.text
        if not raw:
            return None
        data = json.loads(raw)
        if not isinstance(data, list):
            return None
        valid_ids = {int(row["id"]) for row in rows}
        out: dict[int, str] = {}
        for item in data:
            try:
                row_id = int(item["id"])
                category = item.get("category")
            except (KeyError, TypeError, ValueError):
                continue
            if row_id in valid_ids and category in CATEGORIES:
                out[row_id] = category
        return out
    except Exception as exc:  # brief: bad input/keys never crash the tool
        print(
            f"shrek: gemini categorize failed ({type(exc).__name__}); nothing changed",
            file=sys.stderr,
        )
        return None


def list_models(key: str) -> list[str]:
    """Model list for the first-run wizard. Live fetch, curated fallback."""
    fallback = [DEFAULT_MODEL, "gemini-3.8-flash-lite", "gemini-2.5-pro"]
    if not key:
        return fallback
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=key)
        names = []
        for m in client.models.list():
            if getattr(m, "supported_actions", None) is None or "generateContent" in (m.supported_actions or []):
                names.append(m.name.removeprefix("models/"))
        curated = [n for n in names if "flash" in n] or names
        return sorted(dict.fromkeys(curated))[:12] or fallback
    except Exception:
        return fallback
