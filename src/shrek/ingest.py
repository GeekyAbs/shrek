"""Shared ingestion service: persist regex-parseable expenses BEFORE any LLM
call, then let the LLM fill in categories only."""

from __future__ import annotations

import typing as t
from dataclasses import dataclass, field

from shrek import llm
from shrek.config import DEFAULT_MODEL
from shrek.models import CATEGORIES, UNCATEGORIZED, Expense, coerce_category
from shrek.parser import parse_many
from shrek.store import Store

# Sentinel returned by parse_expense meaning "the LLM confidently judged
# this line is not a spend" (income, refund, chit-chat). Callers treat it
# as terminal -- no regex fallback -- so a refund is never swallowed as a debit.


@dataclass
class IngestResult:
    """What the surfaces (CLI, bot) render after one ingestion."""

    expenses: list[Expense] = field(default_factory=list)
    not_expense: bool = False
    error: t.Optional[str] = None


def ingest(
    text: str,
    store: Store,
    key: t.Optional[str] = None,
    model: t.Optional[str] = None,
    default_currency: str = "INR",
    source: str = "cli",
) -> IngestResult:
    """Persist regex-parseable expenses, then categorize them.

    Order is deliberate and never changes:
      1. parse_many splits the line into regex entries,
      2. each entry is inserted immediately (category = miscellaneous,
         source = regex),
      3. one categorize call fills in categories (never amount/currency/
         description),
      4. the saved rows are refetched so the caller sees final categories.
    If no regex entry exists and a key is set, the LLM gets one shot at the
    whole line. Nothing here ever raises.
    """
    result = IngestResult()
    try:
        parsed = parse_many(text, default_currency=default_currency)
    except Exception:
        parsed = None  # type: ignore[assignment]

    entries = getattr(parsed, "entries", None) or []
    skipped = getattr(parsed, "skipped", 0) or 0

    saved_ids: list[int] = []
    saved_rows: list[Expense] = []

    if entries:
        # Step 2 -- write first, LLM second. A write failure returns
        # whatever has been saved so far and does not silently lose a row.
        for entry in entries:
            try:
                expense = Expense(
                    amount=entry.amount,
                    currency=entry.currency,
                    category=UNCATEGORIZED,
                    category_source="regex",
                    description=entry.description,
                    source=source,
                )
                expense.id = store.add(expense)
                saved_rows.append(expense)
                saved_ids.append(int(expense.id))
            except Exception:
                result.error = "could not write the expense"
                break
        # Step 3 -- one batched categorize call. Its failure or None leaves
        # the rows saved as miscellaneous; that is a feature, not a bug.
        if key and saved_ids:
            payload = [
                {
                    "id": expense.id,
                    "description": expense.description,
                    "amount": expense.amount,
                    "currency": expense.currency,
                }
                for expense in saved_rows
            ]
            try:
                mapping = llm.categorize(payload, key=key, model=model or DEFAULT_MODEL)
            except Exception:
                mapping = None
            if isinstance(mapping, dict):
                updates: list[tuple[int, str]] = []
                for expense in saved_rows:
                    category = mapping.get(expense.id)
                    if category in CATEGORIES and category != UNCATEGORIZED:
                        updates.append((int(expense.id), category))
                if updates:
                    try:
                        store.update_categories(updates, source="llm")
                    except Exception:
                        pass
        # Step 4 -- refetch so the caller sees the final categories.
        refetched: list[Expense] = []
        for expense in saved_rows:
            row = store.get(int(expense.id))
            if row is not None:
                refetched.append(row)
        result.expenses = refetched or saved_rows
        return result

    # No regex entry: let the LLM have one shot when a key exists.
    if key:
        try:
            entry = llm.parse_expense(
                text, key=key, model=model or DEFAULT_MODEL,
                default_currency=default_currency,
            )
        except Exception:
            entry = None
        if entry is llm.NOT_EXPENSE:
            result.not_expense = True
            return result
        if entry is not None:
            try:
                expense = Expense(
                    amount=entry.amount,
                    currency=entry.currency,
                    category=coerce_category(entry.category),
                    category_source="llm",
                    description=entry.description,
                    source=source,
                )
                expense.id = store.add(expense)
                row = store.get(int(expense.id))
                result.expenses = [row if row is not None else expense]
                return result
            except Exception:
                result.error = "could not write the expense"
                return result

    # Nothing parseable and no LLM fallback.
    if skipped:
        result.not_expense = True
    else:
        result.error = "no amount found"
    return result
