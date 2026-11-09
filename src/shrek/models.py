"""/models: SQLModel ledger types and the fixed expense taxonomy."""

from datetime import datetime
from typing import Optional

from pydantic import NaiveDatetime
from sqlmodel import Field, SQLModel

#: The fixed preset taxonomy the LLM must pick from (plan Q5).
CATEGORIES: tuple[str, ...] = (
    "food",
    "groceries",
    "transport",
    "rent",
    "bills",
    "shopping",
    "health",
    "fun",
    "travel",
    "other",
)


def fmt_amount(value: float) -> str:
    """Plain decimal, trailing zeros trimmed — 230.5 -> '230.5', 1250000 ->
    '1250000'. Never scientific notation ({:g} gives 1.25e+06 for lakhs)."""
    text = f"{value:.2f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def coerce_category(name: Optional[str]) -> str:
    """Snap a category name to the preset list; unknown/None -> 'other'."""
    if not name:
        return "other"
    n = str(name).strip().lower()
    return n if n in CATEGORIES else "other"


def now_utc() -> datetime:
    """Ledger timestamps are naive LOCAL wall-clock (plan ruling — totals
    follow the user's own calendar). Standardize on naive-local everywhere."""
    return datetime.now()


class Expense(SQLModel, table=True):
    """One swallowed expense. Debit-only ledger for v1."""

    id: Optional[int] = Field(default=None, primary_key=True)
    ts: NaiveDatetime = Field(default_factory=now_utc, index=True)
    amount: float
    currency: str = "INR"
    category: str = "other"
    description: str = ""
    source: str = "cli"  # cli | telegram
