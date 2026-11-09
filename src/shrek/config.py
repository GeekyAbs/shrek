"""User config (~/.shrek/config.toml) and the first-run TUI wizard."""

import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Optional

import tomlkit

CONFIG_DIR = Path(os.environ.get("SHREK_HOME", "~/.shrek")).expanduser()
CONFIG_PATH = CONFIG_DIR / "config.toml"
DEFAULT_MODEL = "gemini-2.5-flash"


def load() -> dict:
    """Config values as a plain dict; {} when no file (or unreadable)."""
    if not CONFIG_PATH.exists():
        return {}
    try:
        return tomlkit.parse(CONFIG_PATH.read_text(encoding="utf-8")).unwrap()
    except Exception:
        print("shrek: config file unreadable; ignoring it", file=sys.stderr)
        return {}


def gemini_key() -> Optional[str]:
    return (load().get("gemini_key") or "").strip() or None


def model() -> str:
    return (load().get("model") or "").strip() or DEFAULT_MODEL


def is_configured() -> bool:
    return gemini_key() is not None


def save(gemini_key: str, model: Optional[str] = None) -> Path:
    """Write the config with owner-only permissions (plan Q17: 0600)."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    doc = tomlkit.parse(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else tomlkit.document()
    doc["gemini_key"] = gemini_key
    if model:
        doc["model"] = model
    CONFIG_PATH.write_text(tomlkit.dumps(doc), encoding="utf-8")
    os.chmod(CONFIG_PATH, stat.S_IRUSR | stat.S_IWUSR)
    return CONFIG_PATH


def ensure_configured() -> bool:
    """Run the first-run wizard if needed. Returns whether a key exists."""
    if is_configured():
        return True
    if not sys.stdin.isatty() or not shutil.which("uv"):  # headless: fail politely
        print(
            "shrek: no Gemini key configured.\n"
            "  run `shrek` in a terminal to walk through first-time setup,\n"
            "  or drop a key into ~/.shrek/config.toml yourself.",
            file=sys.stderr,
        )
        return False
    wizard()
    return is_configured()


def wizard() -> None:
    """Interactive first-run: paste key, pick model. Never echoes the key."""
    print("🧅 shrek first-run setup — let's get you swallowed properly.")
    print("  Your Gemini API key: https://aistudio.google.com/apikey")
    key = ""
    while not key:
        try:
            key = input("paste your Gemini API key (input hidden? no — it is NOT): ").strip()
        except EOFError:
            return
        if not key:
            print("  a key is required for the smart brain (or Ctrl-C to skip).")
    print("\n  picking a model (Enter = gemini-2.5-flash)...")
    from shrek import llm

    models = llm.list_models(key)
    print(f"  {models[0]}  <- default")
    for i, name in enumerate(models[1:], start=1):
        print(f"  {i}. {name}")
    choice = input("model number (or name, or Enter): ").strip()
    model = models[0]
    if choice == "":
        pass
    elif choice.isdigit() and 1 <= int(choice) < len(models):
        model = models[int(choice)]
    elif choice in models:
        model = choice
    else:
        print(f"  not a known model; defaulting to {model}")
    path = save(key, model)
    print(f"\n✅ saved to {path} (0600). Shrek is ready. Type expenses at me!")
