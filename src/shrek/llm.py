"""Gemini structured-JSON parsing of expense lines (the smart swallow)."""

import json
import sys
import typing as t

from shrek.models import CATEGORIES
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

def parse_expense(text: str, key: str, model: str = "gemini-2.5-flash") -> t.Optional[ParsedEntry]:
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
                system_instruction=_PROMPT,
                response_mime_type="application/json",
                response_schema=_EXPENSE_SCHEMA,
                temperature=0.1,
            ),
        )
        raw = response.text
        if not raw:
            return None
        data = json.loads(raw)
        if data.get("type") != "expense":  # income/non-expense: not our ledger's business
            return None
        amount = float(data.get("amount") or 0)
        if amount <= 0:
            return None
        return ParsedEntry(
            amount=amount,
            currency=str(data.get("currency") or "INR").upper() or "INR",
            category=data.get("category") if data.get("category") in CATEGORIES else "other",
            description=str(data.get("description") or "").strip(),
            confidence="llm",
        )
    except Exception as exc:  # brief: bad input/keys never crash the tool
        print(f"shrek: gemini parse failed ({type(exc).__name__}); using fallback", file=sys.stderr)
        return None


def list_models(key: str) -> list[str]:
    """Model list for the first-run wizard. Live fetch, curated fallback."""
    fallback = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro"]
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
