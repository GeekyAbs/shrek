"""The organize flow, shared by the CLI, REPL and Telegram bot.

One Gemini request carries every `miscellaneous` row; one response maps ids
back to categories. Any failure before the first write leaves the ledger
untouched.
"""

from dataclasses import dataclass
from typing import Optional

from shrek import llm
from shrek.config import DEFAULT_MODEL
from shrek.models import CATEGORIES, UNCATEGORIZED, Expense
from shrek.store import Store


@dataclass
class Change:
    """One proposed re-categorization."""

    id: int
    description: str
    old: str
    new: str


@dataclass
class OrganizeReport:
    """Everything a surface needs to render an organize run."""

    candidates: int
    changes: list[Change]
    applied: bool
    error: Optional[str] = None


def organize(
    store: Store,
    key: Optional[str],
    model: Optional[str] = None,
    dry_run: bool = False,
) -> OrganizeReport:
    """Re-categorize every `miscellaneous` row in one batched LLM call."""
    rows: list[Expense] = store.list(category=UNCATEGORIZED)
    if not rows:
        return OrganizeReport(candidates=0, changes=[], applied=not dry_run)
    if not key:
        return OrganizeReport(
            candidates=len(rows),
            changes=[],
            applied=False,
            error="smart brain needed: set GEMINI_API_KEY (shrek setup)",
        )

    payload = [
        {
            "id": row.id,
            "description": row.description,
            "amount": row.amount,
            "currency": row.currency,
        }
        for row in rows
    ]
    mapping = llm.categorize(payload, key=key, model=model or DEFAULT_MODEL)
    if mapping is None:
        return OrganizeReport(
            candidates=len(rows),
            changes=[],
            applied=False,
            error="gemini categorization failed -- nothing changed",
        )

    changes: list[Change] = []
    for row in rows:
        new = mapping.get(row.id)
        if new is None or new == UNCATEGORIZED or new not in CATEGORIES:
            continue
        changes.append(Change(row.id, row.description, row.category, new))

    if dry_run:
        return OrganizeReport(candidates=len(rows), changes=changes, applied=False)

    for change in changes:
        store.update_category(change.id, change.new, source="organized")
    return OrganizeReport(candidates=len(rows), changes=changes, applied=True)
