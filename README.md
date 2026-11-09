# 🧅 shrek — the ogre that swallows your expenses

Talk to shrek like a person; it keeps receipts.

```console
$ shrek
🧅 > ate pizza 200 rupees
✅ food · INR 200 — ate pizza

🧅 > had coffee ₹30, garbage compost 99.50
✅ other · INR 129.5
```

No round numbers? No clean categories? Shrek's **smart brain** (your own
Gemini API key) organizes the messy lines into amounts, currencies and
preset categories. **Offline? No key? Quota burnt?** Shrek still swallows
plain lines like `200 rupees` with a built-in deterministic parser — it
never crashes on bad input, it just doesn't guess.

## The two shreks in one package

| Surface | What it is | Where the ledger lives |
|---|---|---|
| `shrek` | Conversational CLI + classic `add/list/total/delete/undo` | your machine's `~/.shrek/ledger.db` |
| `shrek-bot` | Telegram bot twin, **locked to exactly one owner** (you) | SQLite file in the container's `/data` |

Per the self-hosted ethos: **every person hosts and owns their own shrek**
(own bot token, own DB, own Gemini key — BYOK — so nobody pays for a
stranger's tokens). Shrek's public deployments are exactly as private as
their owner keeps them.

## CLI quickstart

```console
# install (uv)
uv tool install .

# first run walks you through key + model choice (saved to ~/.shrek/config.toml, 0600)
shrek

# or one-shot commands
shrek add "delivery 450 rupees"
shrek list
shrek total                # grand totals, per currency, never franken-summed
shrek total food month     # category + period filters combine
shrek undo                 # spat the last one back out
shrek delete 3
```

Inside the REPL the same words work too (`list`, `total`, `undo`, `exit`).

No key? `shrek` works without one — the regex brain handles plain
`<amount> rupees/$/€/£` lines and tells you when a line is beyond it.

## Host your own Telegram shrek (one container)

1. Create a bot with [@BotFather](https://t.me/BotFather), get its token.
2. Find your numeric Telegram id (e.g. message `@userinfobot`).
3. `cp .env.example .env`, fill in `TELEGRAM_BOT_TOKEN`, `SHREK_OWNER_ID`.
4. Build + run — the ledger persists in a named volume:

```console
docker build -t shrek .
docker run -d --name my-shrek \
  --env-file .env -v shrek-data:/data \
  shrek
```

5. DM your bot: `/start`, then `had coffee 30 rupees`. Every swallow comes
   back with **Undo** and **Change category** inline buttons.
6. Send `/key <your-gemini-key>` whenever you want the smart brain on that
   instance (BYOK — your key, your tokens). `/forgetkey` turns it off again.

Any other Telegram user who messages your bot only ever hears:

> 🧅 this swamp is taken — it belongs to its owner only. Run your own shrek:
> <repo-link>

## How a line becomes a ledger row

1. **Smart brain** (Gemini, structured JSON response schema): amount,
   currency, category from the fixed preset
   (food, groceries, transport, rent, bills, shopping, health, fun, travel, other),
   cleaned description. Income/refund lines are rejected — v1 is debit-only.
2. **Regex brain**: `200 rupees` / `₹200` / `$5` / `12,450 EUR` …
   deterministic and exhaustively tested.
3. **The ledger**: SQLite (via SQLModel). Currencies are recorded
   as-written and never converted; `total` sums per currency, period
   windows are local-calendar (`today | week | month`).

## Tests

```console
uv run pytest
```

The exhaustive matrix lives in `tests/test_parser.py` (regex brain) and
`tests/test_store.py` (ledger). Telegram wiring is smoke-tested manually by
design (locked single instance = tiny blast radius).

## Config reference

`~/.shrek/config.toml` (CLI + bot owner key):

```toml
gemini_key = "..."      # BYOK; never echoed, 0600 on the file
model = "gemini-2.5-flash"
```

`SHREK_HOME` relocates the whole `~/.shrek` home (handy for tests).

Bot env (`.env`): `TELEGRAM_BOT_TOKEN`, `SHREK_OWNER_ID`, `SHREK_DB_PATH`
(default `/data/ledger.db` in Docker, `./shrek.db` natively).

## Why "shrek"

Ogres have layers — like an expense line. Also, they eat everything and
keep the receipt. 🧅
