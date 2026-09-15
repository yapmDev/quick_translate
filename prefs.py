"""What the panel remembers about languages: the pair last used and the short
list the selectors offer before "mostrar todos". No GTK.

Same deal as geometry.py — a missing or corrupt file means "no memory yet", and
a write failure is swallowed: forgetting a language choice is never a reason to
fail the translation that changed it.
"""
import json
from pathlib import Path

import translate as translate_mod

STATE_DIR = Path.home() / ".config" / "quick_translate"
STATE_PATH = STATE_DIR / "languages.json"

# The short list on a machine that has never run this: the pair the panel starts
# on. Everything else lands there by being picked (see TranslateView).
DEFAULT_FAVORITES = (translate_mod.DEFAULT_TARGET, translate_mod.ALTERNATE_TARGET)


def _is_language(code) -> bool:
    return isinstance(code, str) and code in translate_mod.LANGUAGE_NAMES


def load() -> tuple[str, str, list[str]]:
    """(source, target, favorites), with defaults for whatever is unusable.

    Codes that are not in LANGUAGES are dropped rather than carried around —
    they would show up as blank rows in the selectors.
    """
    data = None
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    if not isinstance(data, dict):
        data = {}

    source = data.get("source")
    if source != translate_mod.AUTO and not _is_language(source):
        source = translate_mod.DEFAULT_SOURCE

    target = data.get("target")
    if not _is_language(target):
        target = translate_mod.DEFAULT_TARGET

    raw = data.get("favorites")
    raw = raw if isinstance(raw, list) else []
    favorites = list(dict.fromkeys(code for code in raw if _is_language(code)))

    # An empty short list would leave the selectors with nothing but "mostrar
    # todos", so the defaults come back instead of being remembered as empty.
    return source, target, favorites or list(DEFAULT_FAVORITES)


def save(source: str, target: str, favorites) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(
            json.dumps(
                {
                    "source": source,
                    "target": target,
                    "favorites": list(dict.fromkeys(favorites)),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass
