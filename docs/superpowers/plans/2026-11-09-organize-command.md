# `organize` command + `miscellaneous` bucket — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit `miscellaneous` bucket and an `organize` command that re-categorizes those rows in one Gemini request.

**Architecture:** `miscellaneous` becomes the single uncategorized signal; `category_source` records provenance. A new `shrek.organize` service owns the whole flow and is called by the CLI, REPL and Telegram bot. `Store.total()` and `Store.list(limit)` move their work into SQL. A one-time `PRAGMA user_version` migration adds the column and deletes legacy `other` rows.

**Tech Stack:** Python 3.12, Typer, SQLModel/SQLAlchemy over SQLite, google-genai, python-telegram-bot, pytest.

**Spec:** `docs/superpowers/specs/2026-11-09-organize-command-design.md`

## Global Constraints

- Fixed taxonomy has exactly **11** buckets; the last two are `"other"` and `"miscellaneous"`.
- `UNCATEGORIZED == "miscellaneous"`; unknown/None categories coerce there, never to `"other"`.
- Currencies are never converted; totals stay per currency.
- Timestamps stay naive local wall-clock.
- Bad input, a missing key, or an unreachable Gemini must never crash a surface.
- No new dependencies.
- CLI output is plain text (no emoji).
- Organize sends **one** request for all pending rows; no chunking, no retries, no partial writes.

## Review Focus

- **Legacy DB upgrade:** `create_all` does not add columns to an existing table; the migration must detect and add `category_source`, delete only legacy `other` rows once, and be safe on every later open.
- **Organize response hygiene:** missing, duplicate, extra ids, invalid categories, and non-list JSON must be filtered; never write an unknown id or crash.
- **Single-call failure:** a `None`/malformed Gemini result must leave every row untouched and produce an error report.
- **Regex path:** regex-parsed rows must land in `miscellaneous` with `category_source="regex"`, not in `other`.
- **Fresh vs existing DB:** migration runs after `create_all`; a fresh DB already has the column and only bumps the version.

---

### Task 1: Taxonomy and provenance model

**Files:**
- Modify: `src/shrek/models.py`
- Modify: `tests/test_store.py` (two stale assertions)
- Create: `tests/test_models.py`
- Commit: `docs/superpowers/specs/2026-11-09-organize-command-design.md`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `CATEGORIES: tuple[str, ...]` — 11 entries ending `"other", "miscellaneous"`.
  - `UNCATEGORIZED: str = "miscellaneous"`.
  - `coerce_category(name: Optional[str]) -> str` — returns `UNCATEGORIZED` for None/unknown.
  - `Expense.category: str = UNCATEGORIZED`, `Expense.category_source: str = "unknown"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_models.py`:

```python
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
```

In `tests/test_store.py`, rename and update the two assertions that expect `"other"`:

```python
def test_add_unknown_category_becomes_miscellaneous(store):
    eid = store.add(mk(category="yetti-snacks"))
    assert store.get(eid).category == "miscellaneous"


def test_update_category_coerces_to_preset(store):
    eid = store.add(mk())
    assert store.update_category(eid, "totally-made-up").category == "miscellaneous"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_models.py tests/test_store.py -q`
Expected: FAIL — `CATEGORIES` has 10 entries, `coerce_category` returns `"other"`, and `Expense` has no `category_source`.

- [ ] **Step 3: Implement the model changes**

In `src/shrek/models.py`:

```python
CATEGORIES: tuple[str, ...] = (
    "food", "groceries", "transport", "rent", "bills", "shopping",
    "health", "fun", "travel", "other", "miscellaneous",
)

UNCATEGORIZED: str = "miscellaneous"
```

Change `coerce_category` to return `UNCATEGORIZED` for falsy/unknown names. Add to `Expense`:

```python
category: str = UNCATEGORIZED
category_source: str = "unknown"  # llm | regex | manual | organized | unknown
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS (58 passed, 3 skipped baseline, plus the new tests).

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-11-09-organize-command-design.md \
        src/shrek/models.py tests/test_models.py tests/test_store.py
git commit -m "feat(models): add miscellaneous bucket and category provenance"
```

---

### Task 2: SQL totals and SQL-side limit

**Files:**
- Modify: `src/shrek/store.py`
- Modify: `tests/test_store.py`

**Interfaces:**
- Consumes: `Expense.category`, `Expense.currency`, `Expense.amount`, `coerce_category`.
- Produces: `Store.total(...)` and `Store.list(limit=...)` with unchanged signatures; both now do their filtering/aggregation in SQL.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_store.py`:

```python
from sqlalchemy import event


def test_total_sums_in_sql_not_python(store):
    store.add(mk(amount=200.0, currency="INR"))
    store.add(mk(amount=30.5, currency="INR"))
    store.add(mk(amount=10.0, currency="USD"))
    seen = []

    @event.listens_for(store.engine, "before_cursor_execute")
    def capture(conn, cursor, statement, params, context, executemany):
        seen.append(statement.upper())

    assert store.total() == {"INR": 230.5, "USD": 10.0}
    assert any("SUM(" in s and "GROUP BY" in s for s in seen)


def test_list_limit_is_pushed_into_sql(store):
    for i in range(5):
        store.add(mk(description=f"e{i}"))
    seen = []

    @event.listens_for(store.engine, "before_cursor_execute")
    def capture(conn, cursor, statement, params, context, executemany):
        seen.append(statement.upper())

    assert len(store.list(limit=2)) == 2
    assert any("LIMIT" in s for s in seen)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store.py::test_total_sums_in_sql_not_python tests/test_store.py::test_list_limit_is_pushed_into_sql -v`
Expected: FAIL — no `SUM(`/`GROUP BY` statement is emitted; no `LIMIT` is emitted.

- [ ] **Step 3: Implement the SQL push-down**

In `src/shrek/store.py`, import `func` from `sqlalchemy`. Replace the body of `total()` with a grouped query over the same filters (`category`, `period`):

```python
stmt = (
    select(Expense.currency, func.sum(Expense.amount))
    .group_by(Expense.currency)
)
# append the same category/period WHERE clauses used by _filtered
```

Return `{currency: round(float(total), 2)}` sorted by currency. In `list()`, apply `stmt = stmt.limit(int(limit))` and delete the Python slice.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS, including the existing per-currency and ordering tests.

- [ ] **Step 5: Commit**

```bash
git add src/shrek/store.py tests/test_store.py
git commit -m "perf(store): aggregate totals and push list limits into SQL"
```

---

### Task 3: Migration and `category_source` writes

**Files:**
- Modify: `src/shrek/store.py`
- Modify: `tests/test_store.py`

**Interfaces:**
- Consumes: `Expense.category_source`, `UNCATEGORIZED`.
- Produces:
  - `Store.__init__` calls `self._migrate()` after `create_all`.
  - `Store.update_category(expense_id: int, category: str, source: Optional[str] = None) -> Optional[Expense]` — sets `category_source` only when `source` is passed.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_store.py`:

```python
import sqlite3


def _make_legacy_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE expense ("
        "id INTEGER PRIMARY KEY, ts DATETIME, amount FLOAT NOT NULL, "
        "currency VARCHAR NOT NULL, category VARCHAR NOT NULL, "
        "description VARCHAR NOT NULL, source VARCHAR NOT NULL)"
    )
    conn.execute(
        "INSERT INTO expense (ts, amount, currency, category, description, source) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (NOW, 100.0, "INR", "other", "mystery", "cli"),
    )
    conn.execute(
        "INSERT INTO expense (ts, amount, currency, category, description, source) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (NOW, 250.0, "INR", "food", "pizza", "cli"),
    )
    conn.commit()
    conn.close()


def test_update_category_records_source(store):
    eid = store.add(mk())
    row = store.update_category(eid, "bills", source="manual")
    assert row.category_source == "manual"


def test_update_category_without_source_preserves_it(store):
    eid = store.add(mk())
    store.update_category(eid, "bills", source="manual")
    row = store.update_category(eid, "food")
    assert row.category_source == "manual"


def test_migration_adds_column_deletes_legacy_other_and_bumps_version(tmp_path):
    path = tmp_path / "ledger.db"
    _make_legacy_db(path)
    store = Store(path)
    assert sqlite3.connect(path).execute(
        "SELECT COUNT(*) FROM expense WHERE category = 'other'").fetchone()[0] == 0
    assert [r.description for r in store.list()] == ["pizza"]
    with store.engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA user_version").scalar() == 1
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(expense)")}
    assert "category_source" in cols


def test_migration_is_idempotent_and_future_other_rows_survive(tmp_path):
    path = tmp_path / "ledger.db"
    _make_legacy_db(path)
    Store(path)
    second = Store(path)
    eid = second.add(mk(category="other", description="deliberate"))
    assert second.get(eid).category == "other"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store.py -q -k "source or migration"`
Expected: FAIL — `update_category` takes no `source`; the legacy DB has no `category_source` column; `user_version` stays 0.

- [ ] **Step 3: Implement the migration**

In `Store.__init__`, after `SQLModel.metadata.create_all(self.engine)`, call `self._migrate()`.

```python
def _migrate(self) -> None:
    with self.engine.begin() as conn:
        version = int(conn.exec_driver_sql("PRAGMA user_version").scalar() or 0)
        if version >= 1:
            return
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(expense)")}
        if "category_source" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE expense ADD COLUMN category_source "
                "VARCHAR NOT NULL DEFAULT 'unknown'"
            )
        conn.exec_driver_sql("DELETE FROM expense WHERE category = 'other'")
        conn.exec_driver_sql("PRAGMA user_version = 1")
```

Add `source: Optional[str] = None` to `update_category` and set `row.category_source = source` only when `source is not None`.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/shrek/store.py tests/test_store.py
git commit -m "feat(store): one-time migration and category_source updates"
```

---

### Task 4: Batched Gemini categorizer

**Files:**
- Modify: `src/shrek/llm.py`
- Create: `tests/test_llm.py`

**Interfaces:**
- Consumes: `CATEGORIES`, `UNCATEGORIZED`.
- Produces: `llm.categorize(rows: list[dict], key: str, model: str = DEFAULT_MODEL) -> Optional[dict[int, str]]`.
  - `rows` items: `{"id": int, "description": str, "amount": float, "currency": str}`.
  - Returns id→category for valid input ids only, or `None` on any failure.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_llm.py`:

```python
import json

from shrek import llm


class _Resp:
    def __init__(self, text):
        self.text = text


class _FakeModels:
    def __init__(self, payload):
        self.payload = payload

    def generate_content(self, **kwargs):
        return _Resp(self.payload)


class _FakeClient:
    payload = "[]"

    def __init__(self, api_key):
        self.models = _FakeModels(type(self).payload)


ROWS = [
    {"id": 1, "description": "bus ticket", "amount": 30.0, "currency": "INR"},
    {"id": 2, "description": "mystery", "amount": 10.0, "currency": "INR"},
]


def test_categorize_maps_ids_and_filters_invalid(monkeypatch):
    _FakeClient.payload = json.dumps(
        [
            {"id": 1, "category": "transport"},
            {"id": 2, "category": "not-a-category"},
            {"id": 99, "category": "rent"},
        ]
    )
    monkeypatch.setattr("google.genai.Client", _FakeClient)
    assert llm.categorize(ROWS, key="k") == {1: "transport"}


def test_categorize_without_key_returns_none():
    assert llm.categorize(ROWS, key="") is None


def test_categorize_failure_returns_none(monkeypatch):
    class Boom:
        def __init__(self, api_key):
            raise RuntimeError("no network")

    monkeypatch.setattr("google.genai.Client", Boom)
    assert llm.categorize(ROWS, key="k") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_llm.py -v`
Expected: FAIL — `llm.categorize` does not exist.

- [ ] **Step 3: Implement `categorize`**

Add `_CATEGORIZE_SCHEMA` as an `ARRAY` of `{id: INTEGER, category: STRING enum=CATEGORIES}` and a system prompt that says to use `miscellaneous` when unsure. In `categorize`:

- Return `None` when `key` or `rows` is empty.
- Send `json.dumps(rows)` as the single content item with the same `response_mime_type`/`response_schema`/`temperature=0.1` pattern used by `parse_expense`.
- `json.loads` the response; return `None` unless it is a list.
- Build `valid_ids = {int(r["id"]) for r in rows}`; keep only entries whose id is valid and whose category is in `CATEGORIES`.
- Catch every exception, print the existing stderr note, and return `None`.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/shrek/llm.py tests/test_llm.py
git commit -m "feat(llm): one-shot batched categorization"
```

---

### Task 5: Shared organize service

**Files:**
- Create: `src/shrek/organize.py`
- Create: `tests/test_organize.py`

**Interfaces:**
- Consumes: `Store.list(category=...)`, `Store.update_category(id, category, source=...)`, `llm.categorize`, `UNCATEGORIZED`, `CATEGORIES`.
- Produces:
  - `Change` dataclass: `id: int`, `description: str`, `old: str`, `new: str`.
  - `OrganizeReport` dataclass: `candidates: int`, `changes: list[Change]`, `applied: bool`, `error: Optional[str]`.
  - `organize(store, key: Optional[str], model: Optional[str] = None, dry_run: bool = False) -> OrganizeReport`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_organize.py`:

```python
from datetime import datetime

import pytest

from shrek.models import UNCATEGORIZED, Expense
from shrek.organize import organize
from shrek.store import Store


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path / "ledger.db")


def mk(description, category=UNCATEGORIZED, amount=100.0):
    return Expense(
        amount=amount, currency="INR", category=category,
        description=description, source="cli", ts=datetime.now(),
    )


def test_no_candidates(store):
    store.add(mk("pizza", category="food"))
    rep = organize(store, key="k")
    assert rep.candidates == 0 and rep.changes == [] and rep.error is None


def test_no_key_is_an_error_and_writes_nothing(store):
    eid = store.add(mk("bus ticket"))
    rep = organize(store, key=None)
    assert rep.error and rep.applied is False
    assert store.get(eid).category == UNCATEGORIZED


def test_dry_run_proposes_without_writing(store, monkeypatch):
    eid = store.add(mk("bus ticket"))
    monkeypatch.setattr("shrek.llm.categorize", lambda rows, key, model=None: {eid: "transport"})
    rep = organize(store, key="k", dry_run=True)
    assert rep.applied is False
    assert [(c.old, c.new) for c in rep.changes] == [(UNCATEGORIZED, "transport")]
    assert store.get(eid).category == UNCATEGORIZED


def test_apply_writes_and_stamps_organized(store, monkeypatch):
    eid = store.add(mk("bus ticket"))
    monkeypatch.setattr("shrek.llm.categorize", lambda rows, key, model=None: {eid: "transport"})
    rep = organize(store, key="k")
    assert rep.applied is True
    row = store.get(eid)
    assert row.category == "transport" and row.category_source == "organized"


def test_returned_miscellaneous_is_a_noop(store, monkeypatch):
    eid = store.add(mk("mystery"))
    monkeypatch.setattr("shrek.llm.categorize", lambda rows, key, model=None: {eid: UNCATEGORIZED})
    rep = organize(store, key="k")
    assert rep.changes == []
    assert store.get(eid).category == UNCATEGORIZED


def test_invalid_category_is_filtered(store, monkeypatch):
    eid = store.add(mk("mystery"))
    monkeypatch.setattr("shrek.llm.categorize", lambda rows, key, model=None: {eid: "not-real"})
    rep = organize(store, key="k")
    assert rep.changes == []
    assert store.get(eid).category == UNCATEGORIZED


def test_categorizer_failure_writes_nothing(store, monkeypatch):
    eid = store.add(mk("bus ticket"))
    monkeypatch.setattr("shrek.llm.categorize", lambda rows, key, model=None: None)
    rep = organize(store, key="k")
    assert rep.error and rep.applied is False
    assert store.get(eid).category == UNCATEGORIZED
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_organize.py -v`
Expected: FAIL — `shrek.organize` does not exist.

- [ ] **Step 3: Implement the service**

Create `src/shrek/organize.py` with the two dataclasses and:

1. `rows = store.list(category=UNCATEGORIZED)`.
2. Empty → `OrganizeReport(0, [], applied=not dry_run)`.
3. No key → error report (`"smart brain needed: set GEMINI_API_KEY (shrek setup)"`), `applied=False`.
4. Build `payload` with `id`, `description`, `amount`, `currency`; call `llm.categorize`.
5. `None` → error report (`"gemini categorization failed -- nothing changed"`), `applied=False`.
6. Keep only ids mapped to a category in `CATEGORIES` and different from `UNCATEGORIZED`.
7. `dry_run` → return changes with `applied=False`.
8. Otherwise `store.update_category(c.id, c.new, source="organized")` for each, then return `applied=True`.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/shrek/organize.py tests/test_organize.py
git commit -m "feat(organize): shared service for re-categorizing miscellaneous rows"
```

---

### Task 6: CLI command, REPL, and swallow provenance

**Files:**
- Modify: `src/shrek/cli.py`
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: `shrek.organize.organize`, `OrganizeReport`, `UNCATEGORIZED`.
- Produces:
  - `format_organize_report(report: OrganizeReport) -> str`.
  - Typer command `shrek organize [--dry-run]`.
  - REPL keyword `organize`.
  - `swallow()` stamps `category_source` as `"llm"` when `entry.confidence == "llm"`, else `"regex"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli.py`:

```python
from datetime import datetime

import pytest
from typer.testing import CliRunner

from shrek.cli import app, swallow
from shrek.models import UNCATEGORIZED, Expense
from shrek.parser import ParsedEntry
from shrek.store import Store


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path / "ledger.db")


def mk(description, category=UNCATEGORIZED):
    return Expense(
        amount=100.0, currency="INR", category=category,
        description=description, source="cli", ts=datetime.now(),
    )


def test_swallow_stamps_regex_provenance(store):
    swallow("pizza 200 rupees", store, key=None)
    row = store.list()[0]
    assert row.category == UNCATEGORIZED
    assert row.category_source == "regex"


def test_swallow_stamps_llm_provenance(store, monkeypatch):
    monkeypatch.setattr(
        "shrek.cli.parse",
        lambda text, key=None, model=None: ParsedEntry(
            amount=10.0, currency="INR", category="food",
            description="tea", confidence="llm",
        ),
    )
    swallow("tea 10 bucks", store, key="k")
    row = store.list()[0]
    assert row.category == "food"
    assert row.category_source == "llm"


def test_cli_organize_dry_run_does_not_write(store, monkeypatch):
    eid = store.add(mk("bus ticket"))
    monkeypatch.setattr("shrek.cli.default_store", lambda: store)
    monkeypatch.setattr("shrek.cli.current_key", lambda: "k")
    monkeypatch.setattr("shrek.cli.config.model", lambda: "m")
    monkeypatch.setattr("shrek.llm.categorize", lambda rows, key, model=None: {eid: "transport"})
    result = CliRunner().invoke(app, ["organize", "--dry-run"])
    assert result.exit_code == 0
    assert "transport" in result.output
    assert store.get(eid).category == UNCATEGORIZED


def test_cli_organize_applies_by_default(store, monkeypatch):
    eid = store.add(mk("bus ticket"))
    monkeypatch.setattr("shrek.cli.default_store", lambda: store)
    monkeypatch.setattr("shrek.cli.current_key", lambda: "k")
    monkeypatch.setattr("shrek.cli.config.model", lambda: "m")
    monkeypatch.setattr("shrek.llm.categorize", lambda rows, key, model=None: {eid: "transport"})
    result = CliRunner().invoke(app, ["organize"])
    assert result.exit_code == 0
    assert store.get(eid).category == "transport"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL — no `organize` command; `swallow` leaves `category_source="unknown"`.

- [ ] **Step 3: Implement the CLI changes**

- Import `run_organize` aliased from `shrek.organize`, `OrganizeReport`, and `UNCATEGORIZED`.
- `swallow()`: build `Expense` with `category=entry.category or UNCATEGORIZED` and `category_source="llm" if entry.confidence == "llm" else "regex"`.
- Add `format_organize_report`: error → the error text; zero candidates → `"nothing to organize -- no miscellaneous expenses."`; otherwise a header (`organized N of M` or `would organize N of M` when `applied` is False) plus one `  {id}. {old} -> {new} -- {description}` line per change, and `  nothing changed.` when the list is empty.
- Add the Typer command `organize(dry_run: bool = typer.Option(False, "--dry-run", ...))` calling the service with `key=current_key()` and `model=config.model() if key else None`.
- REPL: add `if word == "organize": return format_organize_report(run_organize(store, key=key, model=model))`, and list it in the `help` text.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/shrek/cli.py tests/test_cli.py
git commit -m "feat(cli): organize command, REPL keyword, swallow provenance"
```

---

### Task 7: Telegram `/organize` and bot provenance

**Files:**
- Modify: `src/shrek/bot.py`
- Create: `tests/test_bot.py`

**Interfaces:**
- Consumes: `shrek.organize.organize`, `OrganizeReport`, `UNCATEGORIZED`.
- Produces:
  - `Bot.organize_text() -> str` — runs the service and formats a capped summary.
  - `/organize` handler in `build()`.
  - `owner_message` stamps `category_source` (`llm`/`regex`).
  - `swap_category` passes `source="manual"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_bot.py`:

```python
from datetime import datetime

import pytest

from shrek.bot import Bot
from shrek.models import UNCATEGORIZED, Expense
from shrek.parser import ParsedEntry
from shrek.store import Store


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path / "bot.db")


def mk(description, category=UNCATEGORIZED):
    return Expense(
        amount=100.0, currency="INR", category=category,
        description=description, source="telegram", ts=datetime.now(),
    )


def test_owner_message_stamps_llm_source(store, monkeypatch):
    monkeypatch.setattr(
        "shrek.bot.parse",
        lambda text, key=None, model=None: ParsedEntry(
            amount=10.0, currency="INR", category="food",
            description="tea", confidence="llm",
        ),
    )
    bot = Bot(store, owner_id=1)
    _, eid = bot.owner_message("tea 10")
    assert store.get(eid).category_source == "llm"


def test_owner_message_stamps_regex_source(store, monkeypatch):
    monkeypatch.setattr(
        "shrek.bot.parse",
        lambda text, key=None, model=None: ParsedEntry(
            amount=10.0, currency="INR", category=None,
            description="tea", confidence="exact",
        ),
    )
    bot = Bot(store, owner_id=1)
    _, eid = bot.owner_message("tea 10")
    row = store.get(eid)
    assert row.category == UNCATEGORIZED
    assert row.category_source == "regex"


def test_swap_category_stamps_manual(store):
    eid = store.add(mk("mystery"))
    bot = Bot(store, owner_id=1)
    bot.swap_category(1, eid, "bills")
    assert store.get(eid).category_source == "manual"


def test_organize_text_reports_and_caps_changes(store, monkeypatch):
    for i in range(25):
        store.add(mk(f"mystery {i}"))
    calls = {"n": 0}

    def fake_categorize(rows, key, model=None):
        return {row["id"]: "transport" for row in rows}

    monkeypatch.setattr("shrek.llm.categorize", fake_categorize)
    bot = Bot(store, owner_id=1)
    out = bot.organize_text()
    assert "organized 25 of 25" in out
    assert "...and 5 more" in out
    assert store.list(category=UNCATEGORIZED) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_bot.py -v`
Expected: FAIL — `Bot.organize_text` does not exist; `category_source` stays `"unknown"`.

- [ ] **Step 3: Implement the bot changes**

- Import `run_organize` aliased from `shrek.organize` and `UNCATEGORIZED`.
- `owner_message`: `category=entry.category or UNCATEGORIZED`, `category_source="llm" if entry.confidence == "llm" else "regex"`.
- `swap_category`: pass `source="manual"` to `update_category`.
- Add `organize_text()`: run the service with `self.store`, `self.owner_key`, `self.model`; error → return it; zero candidates → the empty message; otherwise a header `organized N of M miscellaneous expense(s)`, the first 20 `{id}. {old} -> {new} -- {description}` lines, and `...and K more` when truncated.
- In `build()`, add `CommandHandler("organize", organize_cmd)` where the handler gates on owner and replies with `bot.organize_text()`; add `/organize` to the help text.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/shrek/bot.py tests/test_bot.py
git commit -m "feat(bot): /organize handler and category provenance"
```

---

### Task 8: Documentation

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: the finished behavior from Tasks 1–7.
- Produces: user-facing docs only.

- [ ] **Step 1: Update the category list and quickstart**

- Replace the 10-bucket list with the 11-bucket list, noting `miscellaneous` is the "brain could not decide" bucket and `other` is a confident catch-all.
- Add `shrek organize` and `shrek organize --dry-run` to the Quickstart command block.
- Add `organize` to the REPL in-session command line.
- Add `/organize` to the bot command list and step 6 of the host-your-own section.
- Note the one-time migration: on first launch legacy `other` rows are deleted and `category_source` is added.

- [ ] **Step 2: Verify the commands shown exist**

Run: `uv run shrek --help` and `uv run shrek organize --help`
Expected: both succeed; `organize` appears with `--dry-run`.

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document miscellaneous bucket and organize command"
```

---

## Self-Review

- **Spec coverage:** taxonomy (Task 1), `category_source` (Tasks 1/3/6/7), SQL fixes (Task 2), migration/legacy delete (Task 3), batched categorizer (Task 4), shared service (Task 5), CLI/REPL (Task 6), bot (Task 7), README (Task 8). All spec sections are owned.
- **Step scan:** each task has one failing-test step, one implementation step, one verification step, and one commit. No TBDs or "handle edge cases".
- **Type consistency:** `organize(store, key, model=None, dry_run=False)`, `OrganizeReport.candidates/changes/applied/error`, `Change.id/description/old/new`, `update_category(..., source=None)`, and `llm.categorize(rows, key, model=...)` are spelled identically everywhere they appear.
- **Review Focus:** legacy upgrade (Task 3), response hygiene (Tasks 4/5), single-call failure (Task 5), regex path (Tasks 6/7), fresh-vs-existing DB (Task 3).
- **Proportion:** implementation bodies are omitted except the migration SQL and schema-shaped test data, which the tests do not determine.
