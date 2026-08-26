"""Google's unofficial translation endpoint. No GTK.

The only state kept here is what protects the endpoint from this app: a cache
of answers already received and the cooldown a 429 arms.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

ENDPOINT = "https://translate.googleapis.com/translate_a/single"
# Google answers 429 to `client=gtx` from any IP that has used it a few times
# ("your computer or network may be sending automated queries") — the block is
# on the client id, not on us: the same host, path and dt=t answer 200 for
# dict-chrome-ex, with the same payload shape.
CLIENT = "dict-chrome-ex"
TIMEOUT = 10
# The endpoint takes the text as a query parameter, so the whole request has to
# fit in a URL — past ~5k characters Google answers 413 instead of translating.
MAX_CHARS = 5000

# A 429 arms a local cooldown and nothing goes out until it expires. Retrying
# into a block is useless at best, and the abuse system plausibly counts those
# retries too — either way the panel debounces typing, so without this every
# pause would fire another request at an endpoint already refusing them. Note
# that the block travels with CLIENT, not with our IP (see above), so if this
# ever starts firing again the fix is another client id, not a wait or a VPN.
COOLDOWN_S = 120

# Translating the same text twice is free. The swap button, reopening the panel
# on a clipboard that has not changed, and backspacing back to a word already
# typed all ask for an answer that has arrived before. Bounded because nothing
# ever clears it: the process is long-lived.
CACHE_MAX = 128

# The only pair this app handles. "auto" means "detect, then translate into
# whichever of the two the text is not".
LANGUAGES = ("en", "es")
DEFAULT_TARGET = "es"

# Touched from the worker threads in widgets.py, never under a lock: a dict
# insert and a float assignment are each atomic, and the worst a race can do is
# cache the same answer twice or push the cooldown out by a few milliseconds.
_cache: dict[tuple[str, str], dict] = {}
_blocked_until = 0.0


class TranslationError(Exception):
    pass


def _cooldown_left() -> int:
    """Seconds until the endpoint may be asked again — 0 when it is free."""
    return max(0, round(_blocked_until - time.monotonic()))


def _rate_limit_message(seconds: int) -> str:
    return f"Google limitó las peticiones — reintenta en {seconds} s"


def _request(text: str, source: str, target: str) -> tuple[str, str]:
    global _blocked_until

    waiting = _cooldown_left()
    if waiting:
        raise TranslationError(_rate_limit_message(waiting))

    params = {
        "client": CLIENT,
        "sl": source,
        "tl": target,
        "dt": "t",
        "q": text,
    }
    request = urllib.request.Request(
        f"{ENDPOINT}?{urllib.parse.urlencode(params)}",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            _blocked_until = time.monotonic() + COOLDOWN_S
            raise TranslationError(_rate_limit_message(COOLDOWN_S)) from exc
        raise TranslationError(f"Google respondió {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise TranslationError("Sin conexión con Google") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise TranslationError("Respuesta inesperada de Google") from exc

    try:
        # payload[0] is a list of [translated_chunk, original_chunk, ...] pieces
        # — Google splits long input into sentences and they must be rejoined.
        # payload[2] is the detected source language.
        chunks = [chunk[0] for chunk in payload[0] if chunk and chunk[0]]
        detected = payload[2] or source
    except (IndexError, TypeError) as exc:
        raise TranslationError("Respuesta inesperada de Google") from exc

    return "".join(chunks), detected


def _remember(key: tuple[str, str], result: dict) -> dict:
    _cache[key] = result
    if len(_cache) > CACHE_MAX:
        # Plain FIFO rather than LRU: dicts keep insertion order, and the whole
        # point is to bound the thing, not to model the access pattern.
        del _cache[next(iter(_cache))]
    return dict(result)


def translate(text: str, source: str = "auto") -> dict:
    """Translate between en/es. `source` is "auto", "en" or "es".

    Returns {"text", "source", "target"} where "source" is the language Google
    actually detected — with "auto" that is the only way to know the direction
    the translation went. Answers are cached, so asking twice costs one request.
    """
    if not text.strip():
        return {"text": "", "source": source, "target": DEFAULT_TARGET}
    if len(text) > MAX_CHARS:
        raise TranslationError(f"Texto demasiado largo (máx. {MAX_CHARS} caracteres)")

    key = (text, source)
    cached = _cache.get(key)
    if cached is not None:
        # A copy: the caller owns what it gets and must not be able to edit the
        # cache by editing its result.
        return dict(cached)

    if source in LANGUAGES:
        target = "es" if source == "en" else "en"
        translated, detected = _request(text, source, target)
        return _remember(key, {"text": translated, "source": detected, "target": target})

    # Auto: there is no detect-only endpoint worth a separate round trip, so
    # translate into Spanish first and read the detected language off that
    # answer. Only text that was already Spanish costs a second request.
    translated, detected = _request(text, "auto", DEFAULT_TARGET)
    if detected == "es":
        translated, detected = _request(text, "es", "en")
        return _remember(key, {"text": translated, "source": detected, "target": "en"})
    return _remember(key, {"text": translated, "source": detected, "target": DEFAULT_TARGET})
