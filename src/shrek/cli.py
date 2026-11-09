"""The shrek CLI: one-shot subcommands + the conversational REPL.

Interface contract (plan Q12): bare `shrek` opens the REPL where every line
is an expense; list/total/delete/add are separate one-shot invocations.
"""

import sys
from typing import Optional

import typer

import shrek.config as config
from shrek.models import CATEGORIES, Expense
from shrek.parser import ParsedEntry, parse
from shrek.store import Store

app = typer.Typer(add_completion=False, no_args_is_help=False,
                  help="🧅 shrek — the ogre that swallows your expenses.")

LEDGER_HINT = "categories: " + ", ".join(CATEGORIES)


def default_store() -> Store:
    config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return Store(str(config.CONFIG_DIR / "ledger.db"))


def current_key() -> Optional[str]:
    """Configured key, or run the wizard; headless => None (regex fallback)."""
    if config.ensure_configured():
        return config.gemini_key()
    return None


def swallow(text: str, store: Store, key: Optional[str], model: Optional[str] = None) -> str:
    """Parse + commit one line. Shared by CLI, REPL and the bot."""
    entry = parse(text, key=key, model=model)
    if entry is None:
        return ("I only swallow expenses — no amount+currency in that.\n"
                "try: ate pizza 200 rupees   (categories: " + ", ".join(CATEGORIES) + ")")
    expense = Expense(
        amount=entry.amount,
        currency=entry.currency,
        category=entry.category or "other",
        description=entry.description,
        source="cli",
    )
    expense.id = store.add(expense)
    return format_entry(expense)


def format_entry(entry: Expense) -> str:
    desc = f" — {entry.description}" if entry.description else ""
    return f"✅ {entry.category} · {entry.currency} {entry.amount:g}{desc}"


def format_totals(totals: dict[str, float]) -> str:
    if not totals:
        return "the swamp is empty — nothing swallowed yet."
    return " · ".join(f"{cur} {amount:g}" for cur, amount in totals.items())


def format_list(rows: list[Expense]) -> str:
    if not rows:
        return "nothing swallowed yet."
    return "\n".join(
        f"{r.id}. [{r.category}] {r.currency} {r.amount:g} — {r.description or '(unnamed)'}"
        for r in rows
    )


def undo_last(store: Store) -> str:
    last = store.latest()
    if last is None:
        return "nothing to spit out."
    store.delete(last.id)
    return f"spat it back out: {last.description or f'#{last.id}'} · {last.currency} {last.amount:g}"


# ---- one-shot commands ---------------------------------------------------

@app.command()
def add(text: str):
    """Swallow one expense line: shrek add "ate pizza 200 rupees" """
    store = default_store()
    typer.echo(swallow(text, store, current_key(), config.model()))
    typer.echo("total: " + format_totals(store.total()))


@app.command("list")
def list_cmd():
    """Show the last 10 swallowed expenses."""
    typer.echo(format_list(default_store().list(limit=10)))


@app.command()
def total(
    category: Optional[str] = typer.Argument(None, help="a category, e.g. food"),
    period: Optional[str] = typer.Argument(None, help="today | week | month"),
):
    """Totals per currency, optionally filtered by category and/or period."""
    try:
        out = default_store().total(category=category, period=period)
    except ValueError as exc:
        typer.echo(f"shrek: {exc}", err=True)
        raise typer.Exit(code=1)
    typer.echo(format_totals(out) if out else
               f"nothing to sum in that window. ({LEDGER_HINT})")


@app.command()
def delete(expense_id: int):
    """Delete one swallowed expense by id (ids: shrek list)."""
    if default_store().delete(expense_id):
        typer.echo(f"gone. #{expense_id} never existed.")
    else:
        typer.echo(f"#{expense_id} is not in my swamp. (shrek list shows ids)")


@app.command()
def undo():
    """Delete the most recently swallowed expense."""
    typer.echo(undo_last(default_store()))


# ---- REPL ----------------------------------------------------------------

def _repl_line(line: str, store: Store, key: Optional[str], model: Optional[str]) -> str:
    word = line.strip().lower()
    if word in ("exit", "quit", "q"):
        return "🧅 burp. bye."
    if word in ("",):  # blank line: stand pat
        return "…waiting for expenses. (help | exit)"
    if word == "help":
        return ("type expenses like: ate pizza 200 rupees\n"
                "in-session: list | total | undo | categories | exit")
    if word == "list":
        return format_list(store.list(limit=10))
    if word == "total":
        return format_totals(store.total())
    if word == "undo":
        return undo_last(store)
    if word.startswith("categories"):
        return LEDGER_HINT
    return swallow(line, store, key, model)


def repl() -> None:
    store = default_store()
    key = current_key()
    model = config.model() if key else None
    typer.echo("🧅 shrek is listening. Type expenses ('ate pizza 200 rupees'), 'help', or 'exit'.")
    while True:
        try:
            line = input("🧅 > ").strip()
        except (EOFError, KeyboardInterrupt):
            typer.echo("\n🧅 burp. bye.")
            return
        if not line:
            continue
        out = _repl_line(line, store, key, model)
        typer.echo(out)
        if line.strip().lower() in ("exit", "quit", "q"):
            return


@app.callback(invoke_without_command=True)
def _root(ctx: typer.Context):
    """No subcommand -> the REPL is the interface."""
    if ctx.invoked_subcommand is None:
        repl()
