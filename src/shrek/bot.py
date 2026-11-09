"""The Telegram twin: a locked, personal instance of shrek.

Contract (plan Q1/Q10/Q15/Q16): every self-hosted instance belongs to
exactly one owner (SHREK_OWNER_ID). Strangers get pointed at the repo
to host their own. Owner messages are auto-committed with inline
Undo / Change category buttons. /key stores the owner's Gemini key.
"""

import os
import sys
import threading
from typing import Optional

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import shrek.config as config
from shrek.models import CATEGORIES, Expense, coerce_category, fmt_amount
from shrek.organize import organize as run_organize
from shrek.parser import parse
from shrek.store import Store

REPO_LINK = "https://github.com/YOURNAME/shrek"
GLOBAL_LOCK = threading.Lock()  # sqlite does not love concurrent writes

MISSING_CONFIG = (
    "shrek-bot: TELEGRAM_BOT_TOKEN and SHREK_OWNER_ID required.\n"
    "cp .env.example .env, then paste your BotFather token + your numeric Telegram id."
)
STRANGER_REPLY = (
    "this swamp is taken — it belongs to its owner only.\n"
    "run your own shrek: " + REPO_LINK
)


class Bot:
    """All bot behavior, dependency-injected so it is smoke-testable without
    network. Parse/store/key live behind plain attributes."""

    def __init__(self, store: Store, owner_id: int, token: str = ""):
        self.store = store
        self.owner_id = owner_id
        self.token = token
        self.owner_key: Optional[str] = config.gemini_key()
        self.model = config.model() if self.owner_key else None

    # -- keys ------------------------------------------------------------

    def set_owner_key(self, key: Optional[str]) -> str:
        self.owner_key = key
        self.model = config.model() if key else None
        if key:
            return "smart brain online: lines with messy wording will parse well now."
        return "smart brain off. I will still catch plain 'X rupees' lines."

    # -- gating ----------------------------------------------------------

    def is_owner(self, user_id: int | None) -> bool:
        return user_id is not None and int(user_id) == self.owner_id

    # -- core behaviors, return (text, buttons) for smoke tests -----------

    def owner_message(self, text: str) -> tuple[str, Optional[int]]:
        """Swallow one owner line. Returns (reply, expense_id) — the id goes
        into the reply's inline buttons so Undo/Change-category act on *that*
        row, not on whatever is newest when pressed."""
        entry = parse(text, key=self.owner_key, model=self.model)
        if entry is None:
            return ("I did not get that — I swallow expenses. "
                    "Try like: had coffee 30 rupees  (/help)"), None
        expense = Expense(
            amount=entry.amount,
            currency=entry.currency,
            category=coerce_category(entry.category),
            category_source="llm" if entry.confidence == "llm" else "regex",
            description=entry.description,
            source="telegram",
        )
        with GLOBAL_LOCK:
            expense.id = self.store.add(expense)
        note = "" if self.owner_key else " · /key to use your Gemini key"
        body = f"+ {expense.category} · {expense.currency} {fmt_amount(expense.amount)}"
        if expense.description:
            body += f" — {expense.description}"
        return body + note, expense.id

    def undo_requested(self, user_id: int, expense_id: int) -> tuple[str, None]:
        if not self.is_owner(user_id):
            return STRANGER_REPLY, None
        row = self.store.get(expense_id)
        if row is None:
            return "that one is already gone from the swamp.", None
        with GLOBAL_LOCK:
            self.store.delete(expense_id)
        desc = row.description or f"#{row.id}"
        return f"spat it back out: {desc} · {row.currency} {fmt_amount(row.amount)}", None

    def swap_category(self, user_id: int, expense_id: int, to_category: str) -> tuple[str, None]:
        if not self.is_owner(user_id):
            return STRANGER_REPLY, None
        with GLOBAL_LOCK:
            row = self.store.update_category(expense_id, to_category, source="manual")
        if row is None:
            return "that one is already gone from the swamp.", None
        return f"re-chewed: {row.category} · {row.currency} {fmt_amount(row.amount)}", None

    def organize_text(self) -> str:
        """Re-categorize every miscellaneous row; reply stays under chat limits."""
        report = run_organize(self.store, key=self.owner_key, model=self.model)
        if report.error:
            return f"shrek: {report.error}"
        if report.candidates == 0:
            return "nothing to organize -- no miscellaneous expenses."
        lines = [
            f"organized {len(report.changes)} of {report.candidates} "
            "miscellaneous expense(s)"
        ]
        for change in report.changes[:20]:
            desc = f" -- {change.description}" if change.description else ""
            lines.append(f"{change.id}. {change.old} -> {change.new}{desc}")
        if len(report.changes) > 20:
            lines.append(f"...and {len(report.changes) - 20} more")
        if not report.changes:
            lines.append("nothing changed.")
        return "\n".join(lines)


def build(bot: Bot) -> Application:  # pragma: no cover - live wiring
    """Wire handlers to python-telegram-bot. Owner-only gate is here."""
    app = Application.builder().token(bot.token).build()

    async def gate(update: Update, ctx) -> bool:
        user = update.effective_user
        if not bot.is_owner(getattr(user, "id", None)):
            if update.effective_message:
                await update.effective_message.reply_text(STRANGER_REPLY)
            return False
        return True

    async def start(update: Update, ctx):
        if not await gate(update, ctx):
            return
        await update.effective_message.reply_text(
            "I am your own shrek. Text me expenses like 'had coffee 30 rupees'.\n"
            "/key <your gemini key> enables the smart brain. (/help)"
        )

    async def help_cmd(update: Update, ctx):
        if not await gate(update, ctx):
            return
        await update.effective_message.reply_text(
            "swallow expenses: just type them, e.g. 'groceries 450 rupees'\n"
            "/key <gemini-key> -- your own key, your own tokens (persisted in your .env)\n"
            "/forgetkey -- smart brain off, regex fallback stays\n"
            "Buttons under each swallow: Undo · Change category "
            f"({', '.join(CATEGORIES)})\n"
            "/organize -- re-categorize miscellaneous expenses in one Gemini call\n"
            "Your ledger lives only in this container."
        )

    async def organize_cmd(update: Update, ctx):
        if not await gate(update, ctx):
            return
        await update.effective_message.reply_text(bot.organize_text())

    async def key_cmd(update: Update, ctx):
        if not await gate(update, ctx):
            return
        key = " ".join(ctx.args) if ctx.args else ""
        if not key:
            await update.effective_message.reply_text(
                "send it like: /key Aa1...your-key  "
                "(get one: https://aistudio.google.com/apikey)")
            return
        config.save(key)
        await update.effective_message.reply_text(bot.set_owner_key(key))

    async def forgetkey_cmd(update: Update, ctx):
        if not await gate(update, ctx):
            return
        config.forget_key()
        await update.effective_message.reply_text(bot.set_owner_key(None))

    async def on_message(update: Update, ctx):
        if not await gate(update, ctx):
            return
        text = (update.effective_message.text or "").strip()
        if not text or text.startswith("/"):
            return
        reply, expense_id = bot.owner_message(text)
        if expense_id is None:
            await update.effective_message.reply_text(reply)
            return
        keyboard = [[
            InlineKeyboardButton("Undo", callback_data=f"undo:{expense_id}"),
            InlineKeyboardButton("Change category", callback_data=f"swappick:{expense_id}"),
        ]]
        await update.effective_message.reply_text(reply, reply_markup=InlineKeyboardMarkup(keyboard))

    async def on_button(update: Update, ctx):  # noqa: C901 (three callback shapes in one place)
        query = update.callback_query
        # strangers never 500 and never get to answer silently — always answer
        await query.answer()
        data = query.data or ""
        parts = data.split(":", 1)
        kind = parts[0]
        arg = parts[1] if len(parts) == 2 else ""
        if not bot.is_owner(query.from_user.id):
            await query.edit_message_text(STRANGER_REPLY)
            return
        if kind == "undo" and arg.isdigit():
            reply, _ = bot.undo_requested(query.from_user.id, int(arg))
            await query.edit_message_text(reply)
        elif kind == "swappick" and arg.isdigit():
            rows = [
                [InlineKeyboardButton(c.title(), callback_data=f"swap:{arg}:{c}")]
                for c in CATEGORIES
            ]
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(rows))
        elif kind == "swap" and ":" in arg:
            raw_id, category = arg.split(":", 1)
            if raw_id.isdigit():
                reply, _ = bot.swap_category(query.from_user.id, int(raw_id), category)
                await query.edit_message_text(reply)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("key", key_cmd))
    app.add_handler(CommandHandler("forgetkey", forgetkey_cmd))
    app.add_handler(CommandHandler("organize", organize_cmd))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    return app


def main() -> None:  # pragma: no cover - process entry
    load_dotenv()            # project-local .env (next to the CWD)
    config._load_dotenv_file()  # $SHREK_HOME/.env (the volume in Docker)
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    owner_raw = os.environ.get("SHREK_OWNER_ID", "")
    if not token or not owner_raw.isdigit():
        print(MISSING_CONFIG, file=sys.stderr)
        sys.exit(1)
    db_path = os.environ.get("SHREK_DB_PATH", "shrek.db")
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    bot = Bot(Store(db_path), owner_id=int(owner_raw), token=token)
    print(f"shrek-bot awake. swamp owned by telegram id {owner_raw}; db={db_path}; "
          f"model={config.model()}; brain={'gemini' if config.gemini_key() else 'regex-fallback'}")
    build(bot).run_polling(allowed_updates=["message", "callback_query"])
