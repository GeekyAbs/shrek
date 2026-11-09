# 🧅 shrek

> The ogre that swallows your expenses. Talk to shrek; it keeps receipts.

```console
$ shrek
> ate pizza 200 rupees
✅ food · ₹200

> had coffee ₹30, 3 bottles coke 120
✅ food · ₹150
```

Self-hostable expense tracker: a conversational CLI with BYOK Gemini and a
lockable Telegram bot twin. Status: under construction (v1).

- Swap: CLI (`shrek`) works offline with a built-in regex parser; Gemini makes it smarter.
- Bot: run your own `shrek-bot` in one Docker container, locked to just you.
