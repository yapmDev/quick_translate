"""Google's unofficial translation endpoint. No GTK, no network state kept."""
import json
import urllib.error
import urllib.parse
import urllib.request

ENDPOINT = "https://translate.googleapis.com/translate_a/single"
TIMEOUT = 10
# The endpoint takes the text as a query parameter, so the whole request has to
# fit in a URL — past ~5k characters Google answers 413 instead of translating.
MAX_CHARS = 5000

# The only pair this app handles. "auto" means "detect, then translate into
# whichever of the two the text is not".
LANGUAGES = ("en", "es")
DEFAULT_TARGET = "es"


class TranslationError(Exception):
    pass


def _request(text: str, source: str, target: str) -> tuple[str, str]:
    params = {
        "client": "gtx",
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


def translate(text: str, source: str = "auto") -> dict:
    """Translate between en/es. `source` is "auto", "en" or "es".

    Returns {"text", "source", "target"} where "source" is the language Google
    actually detected — with "auto" that is the only way to know the direction
    the translation went.
    """
    if not text.strip():
        return {"text": "", "source": source, "target": DEFAULT_TARGET}
    if len(text) > MAX_CHARS:
        raise TranslationError(f"Texto demasiado largo (máx. {MAX_CHARS} caracteres)")

    if source in LANGUAGES:
        target = "es" if source == "en" else "en"
        translated, detected = _request(text, source, target)
        return {"text": translated, "source": detected, "target": target}

    # Auto: there is no detect-only endpoint worth a separate round trip, so
    # translate into Spanish first and read the detected language off that
    # answer. Only text that was already Spanish costs a second request.
    translated, detected = _request(text, "auto", DEFAULT_TARGET)
    if detected == "es":
        translated, detected = _request(text, "es", "en")
        return {"text": translated, "source": detected, "target": "en"}
    return {"text": translated, "source": detected, "target": DEFAULT_TARGET}
