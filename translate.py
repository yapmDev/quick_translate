"""Google's unofficial translation endpoint. No GTK.

The only state kept here is what protects the endpoint from this app: a cache
of answers already received and the cooldown a 429 arms.
"""
import json
import time
import unicodedata
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

AUTO = "auto"

# Every language the endpoint takes, as (code, Spanish name), already in the
# order the selectors show them (Spanish alphabetical) — a list this long is
# read, not searched, so the order is part of the data rather than something
# computed at build time. Codes are Google's, not ISO: "iw" for Hebrew, "jw"
# for Javanese and "zh-CN"/"zh-TW" for Chinese are what the endpoint answers to.
LANGUAGES = (
    ("af", "Afrikáans"),
    ("sq", "Albanés"),
    ("de", "Alemán"),
    ("am", "Amárico"),
    ("ar", "Árabe"),
    ("hy", "Armenio"),
    ("az", "Azerbaiyano"),
    ("bn", "Bengalí"),
    ("be", "Bielorruso"),
    ("my", "Birmano"),
    ("bs", "Bosnio"),
    ("bg", "Búlgaro"),
    ("kn", "Canarés"),
    ("ca", "Catalán"),
    ("ceb", "Cebuano"),
    ("cs", "Checo"),
    ("ny", "Chichewa"),
    ("zh-CN", "Chino (simplificado)"),
    ("zh-TW", "Chino (tradicional)"),
    ("si", "Cingalés"),
    ("ko", "Coreano"),
    ("co", "Corso"),
    ("ht", "Criollo haitiano"),
    ("hr", "Croata"),
    ("da", "Danés"),
    ("sk", "Eslovaco"),
    ("sl", "Esloveno"),
    ("es", "Español"),
    ("eo", "Esperanto"),
    ("et", "Estonio"),
    ("eu", "Euskera"),
    ("fi", "Finés"),
    ("fr", "Francés"),
    ("fy", "Frisón"),
    ("gd", "Gaélico escocés"),
    ("cy", "Galés"),
    ("gl", "Gallego"),
    ("ka", "Georgiano"),
    ("el", "Griego"),
    ("gu", "Guyaratí"),
    ("ha", "Hausa"),
    ("haw", "Hawaiano"),
    ("iw", "Hebreo"),
    ("hi", "Hindi"),
    ("hmn", "Hmong"),
    ("hu", "Húngaro"),
    ("ig", "Igbo"),
    ("id", "Indonesio"),
    ("en", "Inglés"),
    ("ga", "Irlandés"),
    ("is", "Islandés"),
    ("it", "Italiano"),
    ("ja", "Japonés"),
    ("jw", "Javanés"),
    ("km", "Jemer"),
    ("kk", "Kazajo"),
    ("ky", "Kirguís"),
    ("ku", "Kurdo"),
    ("lo", "Lao"),
    ("la", "Latín"),
    ("lv", "Letón"),
    ("lt", "Lituano"),
    ("lb", "Luxemburgués"),
    ("mk", "Macedonio"),
    ("ml", "Malayalam"),
    ("ms", "Malayo"),
    ("mg", "Malgache"),
    ("mt", "Maltés"),
    ("mi", "Maorí"),
    ("mr", "Maratí"),
    ("mn", "Mongol"),
    ("nl", "Neerlandés"),
    ("ne", "Nepalí"),
    ("no", "Noruego"),
    ("or", "Oriya"),
    ("pa", "Panyabí"),
    ("ps", "Pastún"),
    ("fa", "Persa"),
    ("pl", "Polaco"),
    ("pt", "Portugués"),
    ("ro", "Rumano"),
    ("ru", "Ruso"),
    ("sm", "Samoano"),
    ("sr", "Serbio"),
    ("st", "Sesoto"),
    ("sn", "Shona"),
    ("sd", "Sindhi"),
    ("so", "Somalí"),
    ("sw", "Suajili"),
    ("sv", "Sueco"),
    ("tl", "Tagalo"),
    ("th", "Tailandés"),
    ("ta", "Tamil"),
    ("tt", "Tártaro"),
    ("tg", "Tayiko"),
    ("te", "Telugu"),
    ("tr", "Turco"),
    ("tk", "Turcomano"),
    ("uk", "Ucraniano"),
    ("ug", "Uigur"),
    ("ur", "Urdu"),
    ("uz", "Uzbeko"),
    ("vi", "Vietnamita"),
    ("xh", "Xhosa"),
    ("yi", "Yidis"),
    ("yo", "Yoruba"),
    ("zu", "Zulú"),
)
LANGUAGE_NAMES = dict(LANGUAGES)

# Where the panel starts: detect the text, put it in Spanish. ALTERNATE_TARGET
# is where it goes instead when detection comes back as the target itself —
# translating Spanish into Spanish is a no-op, and "auto" means "get me out of
# whatever this is" (see TranslateView._on_success, which owns that decision so
# that the selectors always show the direction actually used).
DEFAULT_SOURCE = AUTO
DEFAULT_TARGET = "es"
ALTERNATE_TARGET = "en"


def language_name(code: str) -> str:
    """Display name for a language code — the raw code if Google sent one we
    don't list (its detector answers with codes the translator doesn't offer)."""
    return LANGUAGE_NAMES.get(code, code.upper())


def _fold(text: str) -> str:
    """Lowercased and stripped of accents, so "aleman" can find "Alemán"."""
    decomposed = unicodedata.normalize("NFD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def language_matches(code: str, query: str) -> bool:
    """Does this language answer to `query`? Matches the name or the code, and
    an empty query matches everything — it is a filter, not a search."""
    query = _fold(query.strip())
    if not query:
        return True
    return query in _fold(LANGUAGE_NAMES.get(code, "")) or query in code.casefold()


# Touched from the worker threads in widgets.py, never under a lock: a dict
# insert and a float assignment are each atomic, and the worst a race can do is
# cache the same answer twice or push the cooldown out by a few milliseconds.
_cache: dict[tuple[str, str, str], dict] = {}
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


def _remember(key: tuple[str, str, str], result: dict) -> dict:
    _cache[key] = result
    if len(_cache) > CACHE_MAX:
        # Plain FIFO rather than LRU: dicts keep insertion order, and the whole
        # point is to bound the thing, not to model the access pattern.
        del _cache[next(iter(_cache))]
    return dict(result)


def translate(text: str, source: str = AUTO, target: str = DEFAULT_TARGET) -> dict:
    """Translate `text` from `source` (a language code or "auto") into `target`.

    Returns {"text", "source", "target"} where "source" is the language Google
    actually detected — with "auto" that is the only way to know what was sent.
    Answers are cached, so asking twice costs one request.
    """
    if not text.strip():
        return {"text": "", "source": source, "target": target}
    if len(text) > MAX_CHARS:
        raise TranslationError(f"Texto demasiado largo (máx. {MAX_CHARS} caracteres)")

    key = (text, source, target)
    cached = _cache.get(key)
    if cached is not None:
        # A copy: the caller owns what it gets and must not be able to edit the
        # cache by editing its result.
        return dict(cached)

    translated, detected = _request(text, source, target)
    return _remember(key, {"text": translated, "source": detected, "target": target})
