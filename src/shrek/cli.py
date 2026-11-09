"""The shrek CLI: one-shot subcommands + the conversational REPL.

Interface contract (plan Q12): bare `shrek` opens the REPL where every line
is an expense; add/list/total/delete/undo are separate one-shot invocations.
Configuration is plain env vars (GEMINI_API_KEY, GEMINI_MODEL) with a
generated $SHREK_HOME/.env fallback -- see shrek.config.
"""

import re
from typing import Optional

import typer

import shrek.config as config
from shrek.ingest import ingest
from shrek.models import CATEGORIES, Expense, coerce_category, fmt_amount
from shrek.organize import OrganizeReport, organize as run_organize
from shrek.parser import needs_default_currency
from shrek.store import Store

app = typer.Typer(add_completion=False, no_args_is_help=False,
                  help="shrek -- the ogre that swallows your expenses.")

LEDGER_HINT = "categories: " + ", ".join(CATEGORIES)


def default_store() -> Store:
    config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return Store(str(config.CONFIG_DIR / "ledger.db"))


def current_key() -> Optional[str]:
    """Configured key, or run the setup wizard; headless => None (regex
    fallback), never a crash."""
    if config.ensure_configured():
        return config.gemini_key()
    return None


def swallow(text: str, store: Store, key: Optional[str], model: Optional[str] = None,
            default_currency: str = "INR") -> str:
    """Parse + commit one line. Shared by CLI, REPL and the bot."""
    result = ingest(text, store, key=key, model=model,
                    default_currency=default_currency, source="cli")
    if result.not_expense:
        return "that looks like income/refund, not a spend -- nothing saved."
    out_lines = [format_entry(e) for e in result.expenses]
    if result.error:
        out_lines.append(f"shrek: {result.error}")
    if out_lines:
        return "\n".join(out_lines)
    return ("I only swallow expenses -- no amount+currency in that.\n"
            "try: ate pizza 200 rupees   (" + LEDGER_HINT + ")")


def format_entry(entry: Expense) -> str:
    desc = f" -- {entry.description}" if entry.description else ""
    return f"+ {entry.category} · {entry.currency} {fmt_amount(entry.amount)}{desc}"


def format_totals(totals: dict[str, float]) -> str:
    if not totals:
        return "the swamp is empty -- nothing swallowed yet."
    return " · ".join(f"{cur} {fmt_amount(amount)}" for cur, amount in totals.items())


def format_list(rows: list[Expense]) -> str:
    if not rows:
        return "nothing swallowed yet."
    return "\n".join(
        f"{r.id}. [{r.category}] {r.currency} {fmt_amount(r.amount)} -- {r.description or '(unnamed)'}"
        for r in rows
    )


def format_organize_report(report: OrganizeReport) -> str:
    if report.error:
        return f"shrek: {report.error}"
    if report.candidates == 0:
        return "nothing to organize -- no miscellaneous expenses."
    verb = "organized" if report.applied else "would organize"
    lines = [
        f"{verb} {len(report.changes)} of {report.candidates} miscellaneous expense(s)"
    ]
    for change in report.changes:
        desc = f" -- {change.description}" if change.description else ""
        lines.append(f"  {change.id}. {change.old} -> {change.new}{desc}")
    if not report.changes:
        lines.append("  nothing changed.")
    return "\n".join(lines)


def undo_last(store: Store) -> str:
    last = store.latest()
    if last is None:
        return "nothing to spit out."
    store.delete(last.id)
    return f"spat it back out: {last.description or f'#{last.id}'} · {last.currency} {fmt_amount(last.amount)}"


# ---- one-shot commands ---------------------------------------------------

@app.command()
def setup():
    """First-time setup: saves GEMINI_API_KEY + GEMINI_MODEL + default currency to $SHREK_HOME/.env."""
    config.wizard()


@app.command()
def currency(code: Optional[str] = typer.Argument(None, help="3-letter currency code, e.g. INR")):
    """Show or change the default currency for amounts written without one."""
    if code is None or code.strip() == "":
        current = config.currency()
        typer.echo(f"default currency: {current or 'INR (unset)'}")
        return
    value = code.strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", value):
        typer.echo("shrek: send a three-letter currency code, e.g. shrek currency INR", err=True)
        raise typer.Exit(code=1)
    config.save_currency(value)
    typer.echo(f"default currency: {value}")


@app.command()
def add(text: str):
    """Swallow one expense line: shrek add "ate pizza 200 rupees" """
    store = default_store()
    default_currency = (
        config.ensure_currency() if needs_default_currency(text)
        else (config.currency() or "INR")
    )
    typer.echo(swallow(text, store, current_key(), config.model(), default_currency))
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
    if category is not None and category.strip().lower() not in CATEGORIES:
        typer.echo(f"shrek: '{category}' is not a swamp category.", err=True)
        typer.echo(LEDGER_HINT, err=True)
        raise typer.Exit(code=1)
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


@app.command()
def organize(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview the changes without writing."
    ),
):
    """Re-categorize every miscellaneous expense in one Gemini request."""
    key = current_key()
    report = run_organize(
        default_store(),
        key=key,
        model=config.model() if key else None,
        dry_run=dry_run,
    )
    typer.echo(format_organize_report(report))
    if report.error:
        raise typer.Exit(code=1)


@app.command()
def model(
    name: Optional[str] = typer.Argument(None, help="Gemini model id, e.g. gemini-3.8-flash"),
):
    """Show or change the smart brain's model (updates GEMINI_MODEL in $SHREK_HOME/.env)."""
    if name is None or name.strip() == "":
        typer.echo(f"current model: {config.model()}")
        key = config.gemini_key()
        if key:
            from shrek import llm

            typer.echo("available (live from your key):\n  " + "\n  ".join(llm.list_models(key)))
        return
    value = name.strip()
    if not value.startswith("gemini"):
        typer.echo(f"shrek: '{value}' does not look like a Gemini model id (e.g. gemini-3.8-flash).", err=True)
        raise typer.Exit(code=1)
    path = config.save_model(value)
    typer.echo(f"smart brain now: {value}.")
    typer.echo(f"written: {path} -- or set GEMINI_MODEL= in your environment to override it.")


# ---- REPL ----------------------------------------------------------------

def _repl_line(line: str, store: Store, key: Optional[str], model: Optional[str],
                default_currency: str = "INR") -> str:
    word = line.strip().lower()
    if word in ("exit", "quit", "q"):
        return "burp. bye."
    if word == "":  # blank line: stand pat
        return "...waiting for expenses. (help | exit)"
    if word == "help":
        return ("type expenses like: ate pizza 200 rupees\n"
                "in-session: list | total | undo | organize | categories | "
                "currency <code> | exit")
    if word == "list":
        return format_list(store.list(limit=10))
    if word == "total":
        return format_totals(store.total())
    if word == "undo":
        return undo_last(store)
    if word == "organize":
        return format_organize_report(run_organize(store, key=key, model=model))
    if word.startswith("categories"):
        return LEDGER_HINT
    if word.startswith("currency"):
        parts = line.split()
        if len(parts) == 1:
            return f"default currency: {config.currency() or 'INR (unset)'}"
        value = parts[1].strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", value):
            return "shrek: send a three-letter currency code, e.g. currency INR"
        config.save_currency(value)
        return f"default currency: {value}"
    return swallow(line, store, key, model, config.currency() or default_currency)


def repl() -> None:
    store = default_store()
    key = current_key()
    model = config.model() if key else None
    default_currency = config.ensure_currency()
    typer.echo("shrek is listening. type expenses ('ate pizza 200 rupees'), 'help', or 'exit'.")
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            typer.echo("\nburp. bye.")
            return
        if not line:
            continue
        out = _repl_line(line, store, key, model, default_currency)
        typer.echo(out)
        if line.strip().lower() in ("exit", "quit", "q"):
            return


@app.callback(invoke_without_command=True)
def _root(ctx: typer.Context):
    """No subcommand -> the REPL is the interface."""
    if ctx.invoked_subcommand is None:
        repl()
