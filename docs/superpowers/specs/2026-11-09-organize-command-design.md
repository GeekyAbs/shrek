# `organize` command + an explicit `miscellaneous` bucket

Date: 2026-11-09

## Intent

The ledger already has a catch-all category (`other`), but it is overloaded:
it is a real category the LLM may choose, the default value, and the silent
failure bucket for regex-only rows and invalid input. There is no stored
record of *why* a row is uncategorized, so nothing can safely re-chew the
truly unknown rows.

This change makes `miscellaneous` the explicit "the brain could not decide"
bucket, keeps `other` as a confident catch-all, and adds `organize`: one
Gemini request carrying every `miscellaneous` row, one response mapping ids
back to categories, applied in place.

## Goals

- Fixed taxonomy of **11** buckets: the existing 10 plus `miscellaneous`.
- The LLM always picks from the fixed list, using `miscellaneous` when it
  cannot confidently categorize.
- Regex-parsed rows and any unknown/None category land in `miscellaneous`.
- Record per-row provenance in a new `category_source` column:
  `llm | regex | manual | organized | unknown`.
- `shrek organize` (CLI), `organize` (REPL), and `/organize` (Telegram) share
  one service implementation.
- Organize applies by default; CLI gets `--dry-run`.
- One request for **all** pending rows — no chunking.
- Fix `Store.total()` to sum in SQL and `Store.list(limit)` to push `LIMIT`
  into SQL.
- One-time migration: add `category_source`, delete legacy `other` rows,
  bump `PRAGMA user_version` to 1.

## Non-goals

- No categories beyond `miscellaneous`.
- No currency conversion; totals stay per currency.
- No automatic organize on startup.
- No `organized_at` column.
- No batch chunking, retries, or per-row fallback.
- No change to `float` money storage (noted as future work).

## Anti-goals

- A failed or malformed organize response must never write partial data.
- Organize must never touch a row the user changed by hand (`manual`).
- Bad input, no key, or an unreachable Gemini must never crash a surface.

## Settled decisions

| Decision | Choice |
|---|---|
| Taxonomy | Keep `other`, add `miscellaneous` as bucket 11 |
| Uncategorized signal | `category = "miscellaneous"` |
| Provenance | `category_source` only; no timestamp |
| Legacy `other` rows | Delete once during migration |
| Migration gate | SQLite `PRAGMA user_version` (0 → 1) |
| Organize write mode | Apply by default; CLI `--dry-run` previews |
| Organize brain | One Gemini call for every pending row |
| Response contract | `[{id, category}]`, mapped back by id |
| Failure mode | Whole run writes nothing; report the error |
| Surfaces | CLI + REPL + bot via a shared service module |
| Bot behaviour | Apply immediately, reply with summary |
| SQL fixes | `total()` GROUP BY SUM and `list()` LIMIT push-down |

## Approaches considered

1. **Per-row Gemini calls** — reliable, easy to attribute failures, but one
   HTTP call per row. Rejected: descriptions are ~10 words, so a single
   payload is small and cheaper.
2. **Chunked batches (25 rows)** — resilient to oversized requests, but
   pointless at this data size and adds partial-success semantics. Rejected.
3. **One batched call (chosen)** — all pending rows in, all ids/categories
   out. One failure = one clean "nothing happened".

Taxonomy alternatives considered: replace `other` with `miscellaneous`
(fewer near-duplicates, but loses a bucket and needs a data rename), or make
`other` itself the fallback (no new bucket, keeps the ambiguity). Chosen:
keep both, with distinct meanings.

Legacy alternatives considered: migrate `other` → `miscellaneous` (preserves
rows), organize both buckets forever (re-chews deliberate `other` rows
repeatedly), or leave them alone (legacy rows never organized). Chosen:
delete once, per explicit user instruction.

## How it will work

### Models

- `CATEGORIES` gains `"miscellaneous"`, placed last.
- `UNCATEGORIZED = "miscellaneous"` constant.
- `coerce_category(None | unknown)` returns `UNCATEGORIZED` instead of
  `"other"`.
- `Expense` gains `category_source: str = "unknown"`.

### Store

- `total()` becomes `SELECT currency, SUM(amount) ... GROUP BY currency`.
- `list()` applies `LIMIT` in SQL instead of slicing in Python.
- `update_category(expense_id, category, source=None)` sets
  `category_source` when `source` is provided; existing callers keep working.
- `Store.__init__` runs `_migrate()` after `create_all`:
  1. Read `PRAGMA user_version`.
  2. If `< 1`: add `category_source` when the column is missing
     (`PRAGMA table_info`), `DELETE FROM expense WHERE category = 'other'`,
     then set `user_version = 1`.
  This is idempotent; a fresh DB simply sets the version.

### LLM

- New `llm.categorize(rows, key, model)` where `rows` is a list of
  `{id, description, amount, currency}`.
- Structured response schema: `ARRAY` of `{id: INTEGER, category: STRING}`
  with `category` enum = `CATEGORIES`.
- Prompt: categorize each row, use `miscellaneous` when unsure.
- Returns `{id: category}` for ids present in the input; unknown ids and
  invalid categories are ignored.
- Returns `None` on any failure; the service turns that into a no-write error.

### Service

New `src/shrek/organize.py` with a single entry point used by every surface:

```python
@dataclass
class OrganizeReport:
    candidates: int
    changes: list[Change]   # id, description, old, new
    applied: bool
    error: str | None
```

- Loads `store.list(category="miscellaneous")`.
- No candidates → empty success report.
- No key → error report ("smart brain needed").
- Calls `llm.categorize`.
- Drops no-ops (returned `miscellaneous`) and anything invalid.
- `dry_run=True` returns changes without writing.
- Otherwise updates each change with `source="organized"` and reports
  `applied=True`.
- Any failure before writes → `applied=False`, `error` set, no rows changed.

### Surfaces

- CLI: `shrek organize [--dry-run]` prints counts and a `old → new` line per
  change.
- REPL: `organize` applies and prints the same report.
- Bot: `/organize` applies and replies with a capped summary.
- `swallow()` and the bot's message handler stamp `category_source`:
  `llm` when `entry.confidence == "llm"`, else `regex`.
- Bot's Change-category button stamps `manual`.

### Tests (TDD)

- Taxonomy: 11 buckets, `coerce_category` unknown → `miscellaneous`.
- Store: `total()` sums correctly; `list(limit)` returns only N; migration
  deletes legacy `other`, adds the column, sets `user_version`, and does not
  delete future `other` rows.
- Organize: candidate selection, dry-run writes nothing, apply writes and
  stamps `organized`, no-op on returned `miscellaneous`, malformed/None
  response leaves every row untouched, no key produces an error report.
- LLM `categorize`: monkeypatched client returns ids/categories; invalid ids
  and categories are filtered.

### Docs

README gains the 11-bucket list and `organize` usage for CLI/REPL/bot.

## Left to the builder

- Exact report wording and the bot summary line cap.
- Migration helper details (`PRAGMA table_info` / `user_version` plumbing).
- Test names and fixture shape for the fake LLM.
