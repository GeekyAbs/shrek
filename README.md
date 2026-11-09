# shrek -- the ogre that swallows your expenses

Talk to shrek like a person; it keeps receipts. An LLM organizes the messy
lines (your own Gemini API key), a deterministic parser handles the simple
ones, and bad input never crashes it.

```console
$ shrek
shrek is listening. type expenses ('ate pizza 200 rupees'), 'help', or 'exit'.
> ate pizza 200 rupees
+ food · INR 200 -- ate pizza

> groceries 450.25 ts
+ groceries · INR 450.25 -- groceries ts
```

## The two shreks in one package

| Surface | What it is | Ledger |
|---|---|---|
| `shrek` | Conversational checker plus `add/list/total/delete/undo/organize` | your machine's `~/.shrek/ledger.db` |
| `shrek-bot` | Telegram bot, locked to exactly one owner (you) | SQLite at `SHREK_DB_PATH` (`/data/ledger.db` in Docker) |

Self-host semantics: every person runs and owns their own shrek (own bot
token, own database, own Gemini key), so nobody pays for anyone's tokens
and no stranger ever writes to your swamp.

## Quickstart (CLI)

```console
uv tool install .
shrek setup            # interactive: saves GEMINI_API_KEY + GEMINI_MODEL + default currency to ~/.shrek/.env (0600)
shrek                  # opens the REPL; or one-shot commands below
shrek currency INR     # show or change the default currency for bare amounts

shrek add "delivery 450 rupees"
shrek add "rickshaw 50"                       # bare amount -> default currency
shrek add "20 for rickshaw, 10 for bus"        # one line, two rows
shrek list
shrek total            # totals per currency, never franken-summed
shrek total food month # category and period filters combine
shrek undo             # spit the last one back out
shrek delete 3
shrek organize         # re-categorize every miscellaneous row in one Gemini call
shrek organize --dry-run  # preview the changes, write nothing
# in the REPL: list | total | undo | organize | categories | currency <code> | exit
```

No key? `shrek` still works -- the regex brain parses plain `<number> rupees`
lines (or `$`/`€`/`£` amounts) and tells you when a line is beyond it. Those
rows land in `miscellaneous`, and `shrek organize` needs a key to move them.
The
LLM catches the messy wording.

## Changing the model (or key)

Configuration is just environment variables -- they pick up everywhere
(CLI, REPL, bot), in this precedence: **real environment > `$SHREK_HOME/.env`**:

```console
# everywhere, this shell or profile
export GEMINI_API_KEY=...          # the key
export GEMINI_MODEL=gemini-3.8-flash

# or just edit the generated file
$EDITOR ~/.shrek/.env              # SHREK_HOME=/data inside the container
```

Or: `shrek model` shows the current model and the live list from your key;
`shrek model gemini-3.8-flash-lite` writes GEMINI_MODEL to `~/.shrek/.env`.

Inside the Docker bot, edit `/data/.env` (same volume as the ledger) and
restart the container.

## Host your own Telegram shrek (one container)

1. Create a bot with @BotFather, note the token.
2. Find your numeric Telegram id (e.g. message @userinfobot).
3. `cp .env.example .env` and fill `TELEGRAM_BOT_TOKEN`, `SHREK_OWNER_ID`
   (and optionally `GEMINI_API_KEY`, `GEMINI_MODEL`).
4. Build and run -- ledger and `.env-copied key persist in the volume:

```console
docker build -t shrek .
docker run -d --name my-shrek \
  --env-file .env -v shrek-data:/data \
  shrek
```

5. DM the bot: `/start`, then `had coffee 30 rupees`. Each swallow gets
   **Undo** and **Change category** buttons (both act on that exact row).
   `/currency <code>` sets the default currency for bare amounts (reads and
   writes `SHREK_DEFAULT_CURRENCY` in `/data/.env`).
6. `/key <gemini-key>` turns the smart brain on right from chat -- it is
   written to `/data/.env`, so it survives restarts. `/forgetkey` turns
   it off again. `/organize` re-categorizes every `miscellaneous` row in
   one Gemini call and replies with a capped summary.

Any other Telegram user who messages your bot only ever hears:

> this swamp is taken -- it belongs to its owner only. Run your own shrek
> at https://github.com/YOURNAME/shrek

## How a line becomes a ledger row

1. **Regex-first write**: the deterministic parser splits a message into
   entries (`20 for rickshaw, 10 for bus` -> two rows) and commits each
   parseable expense to SQLite **before** any Gemini call, with
   `category = miscellaneous` and `category_source = regex`. Bare amounts
   (`rickshaw 50`, `20 for rickshaw`) use the user's default currency.
   Refund/income segments (`refund`, `salary`, `cashback`, ...) are skipped
   by the regex but still judged by the LLM when a key exists.
2. **Category fill**: one batched `categorize` call asks Gemini for a category
   per saved row. A Gemini failure, hang, or a killed process leaves the
   rows saved as `miscellaneous` -- nothing is lost. The LLM may change
   **only** the category; amount, currency and description stay exactly as
   saved.
3. **LLM fallback**: lines the regex cannot parse get one `parse_expense`
   call when a key exists; `not_expense`/income verdicts are terminal.
4. **The ledger**: SQLite via SQLModel. Currencies are recorded as-written
   and never converted; `total` sums per currency in SQL; period windows
   (`today | week | month`) follow your local calendar. Each row records
   `category_source` (`llm | regex | manual | organized | unknown`).
5. **Upgrading** from a pre-organize build runs a one-time migration:
   `category_source` is added, legacy `other` rows are deleted, and
   `PRAGMA user_version` moves to 1.

## Tests

```console
uv run pytest
```

Exhaustive matrix in `tests/test_parser.py` (regex brain) and
`tests/test_store.py` (ledger). Telegram wiring is smoke-tested manually by
design (locked single-owner = tiny blast radius).

## Config reference

Variables (same names everywhere):

```console
GEMINI_API_KEY=...            # BYOK; never rotated into logs, file saved 0600
GEMINI_MODEL=gemini-3.8-flash # any Gemini generateContent model id
SHREK_HOME=~/.shrek           # relocate ledger + .env (set in Docker to /data)
SHREK_DEFAULT_CURRENCY=INR    # default currency for bare amounts (chosen on first run)
SHREK_DB_PATH=/data/ledger.db # bot ledger only
SHREK_OWNER_ID=123456789      # bot access lock
TELEGRAM_BOT_TOKEN=...        # from BotFather
```

## Why "shrek"

Ogres have layers -- like an expense line. Also, they eat everything and
keep the receipt.
