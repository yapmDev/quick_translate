"""Google's unofficial text-to-speech endpoint and the player that puts its
answer through the speakers. No GTK — network, bytes and a GStreamer pipeline.

Two things here are the endpoint's rules rather than ours: it takes at most 200
characters per request, and it has no voice for every language the translator
knows. The first is why a translation is spoken as a queue of chunks; the
second is why `has_voice` exists at all.
"""
import urllib.error
import urllib.parse
import urllib.request

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gio, GLib, Gst

import translate as translate_mod

ENDPOINT = "https://translate.google.com/translate_tts"
# The same client id as the translator, and for the same reason: the id is what
# Google's abuse system keys on. This endpoint wants no `tk` signature — host,
# path, `q`, `tl` and a client id are the whole request. Verified answering 200
# for tw-ob, gtx and dict-chrome-ex alike, so the id is shared with translate.py
# rather than picked separately: one id, one 429 budget, one cooldown.
CLIENT = translate_mod.CLIENT
TIMEOUT = 10

# The endpoint's hard limit on `q`: 200 characters answer 200 OK, 201 answer
# 400. `textlen`/`idx`/`total` (what Chrome's own extension sends) do not lift
# it, so a longer text is spoken as several requests whose MP3s are played as
# one — see `audio`.
CHUNK_CHARS = 200
# Ours: every 200 characters is one more request at an endpoint that answers
# 429s, and nobody listens to a five-thousand-character wall anyway.
MAX_CHARS = 1000
# Where a chunk would rather end, best first: a break mid-sentence is audible.
BREAKS = ("\n", ". ", "? ", "! ", "; ", ", ", " ")

# Audio is bulkier than text (~10 KB for a short phrase), so this cache is
# bounded by bytes rather than by entries. Same idea as the translator's: the
# process is long lived and replaying a phrase must not cost a request.
CACHE_MAX_BYTES = 1_000_000

# Mono 24 kHz MP3 comes back; decodebin picks the mp3 decoder and the sink
# whatever the session runs (PipeWire here). giostreamsrc is what lets the
# bytes stay in memory: nothing this app speaks touches the disk.
PIPELINE = "giostreamsrc name=src ! decodebin ! audioconvert ! audioresample ! autoaudiosink"

# Touched from the worker threads in widgets.py, never under a lock — same
# reasoning as translate.py: the worst a race can do is fetch a phrase twice.
_cache: dict[tuple[str, str], bytes] = {}
_cache_bytes = 0
# Languages Google answered 400 for: the translator lists a hundred languages
# and only some have a voice, with no endpoint to ask in advance. So the answer
# is learned from the refusal and remembered, which is what lets the button go
# insensitive instead of failing again on the next click.
_voiceless: set[str] = set()


class SpeechError(Exception):
    pass


def has_voice(lang: str) -> bool:
    """False only for languages already known to have no voice: nothing is
    asked of the network here, this is what previous refusals taught us."""
    return lang not in _voiceless


def chunks(text: str) -> list[str]:
    """`text` split into pieces the endpoint will accept, cut at the latest
    sentence or word break that still fits — a chunk boundary is a pause in the
    playback, so it should fall where the text already pauses."""
    pieces = []
    rest = text.strip()
    while len(rest) > CHUNK_CHARS:
        window = rest[:CHUNK_CHARS]
        cut = -1
        for mark in BREAKS:
            cut = window.rfind(mark)
            if cut > 0:
                cut += len(mark)
                break
        if cut <= 0:
            # A single word longer than the limit: it has to be cut somewhere.
            cut = CHUNK_CHARS
        pieces.append(rest[:cut].strip())
        rest = rest[cut:].lstrip()
    if rest:
        pieces.append(rest)
    return [piece for piece in pieces if piece]


def audio(text: str, lang: str) -> bytes:
    """The MP3 for `text` read in `lang`, blocking — call it off the main loop.

    Long text is several requests, joined into one buffer: MP3 is a stream of
    frames, so concatenating the answers plays back as a single sound.
    """
    text = text.strip()
    if not text:
        return b""
    if len(text) > MAX_CHARS:
        raise SpeechError(f"Texto demasiado largo para escuchar (máx. {MAX_CHARS} caracteres)")

    key = (text, lang)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    data = b"".join(_fetch(chunk, lang) for chunk in chunks(text))
    return _remember(key, data)


def _remember(key: tuple[str, str], data: bytes) -> bytes:
    global _cache_bytes
    _cache[key] = data
    _cache_bytes += len(data)
    while _cache_bytes > CACHE_MAX_BYTES and _cache:
        # FIFO, like the translator's cache: the point is a bound, not a model
        # of how the user replays things.
        _cache_bytes -= len(_cache.pop(next(iter(_cache))))
    return data


def _fetch(chunk: str, lang: str) -> bytes:
    waiting = translate_mod.cooldown_left()
    if waiting:
        raise SpeechError(translate_mod.rate_limit_message(waiting))

    params = {
        "ie": "UTF-8",
        "client": CLIENT,
        "tl": lang,
        "q": chunk,
    }
    request = urllib.request.Request(
        f"{ENDPOINT}?{urllib.parse.urlencode(params)}",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise SpeechError(
                translate_mod.rate_limit_message(translate_mod.note_rate_limit())) from exc
        if exc.code == 400:
            # Chunking already keeps `q` inside the limit, so the request is
            # well formed and the language is what is left to blame.
            _voiceless.add(lang)
            raise SpeechError(
                f"{translate_mod.language_name(lang)} no tiene voz disponible") from exc
        raise SpeechError(f"Google respondió {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise SpeechError("Sin conexión con Google") from exc


# ---- playback --------------------------------------------------------
# One sound at a time: the panel shows one translation, so starting a new one
# replaces whatever was playing instead of talking over it.
_pipeline = None
_finished_cb = None


def is_playing() -> bool:
    return _pipeline is not None


def play(data: bytes, on_finished=None):
    """Play `data` now, replacing whatever was playing. `on_finished` is called
    on the main loop with None when the sound ends or a message if it failed —
    never when `stop()` cut it short, since that one the caller asked for."""
    global _pipeline, _finished_cb

    stop()
    if not data:
        return
    if not Gst.is_initialized():
        Gst.init(None)

    pipeline = Gst.parse_launch(PIPELINE)
    # new_from_bytes rather than new_from_data: the stream then owns a copy and
    # cannot outlive the buffer it was handed.
    pipeline.get_by_name("src").set_property(
        "stream", Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(data)))
    bus = pipeline.get_bus()
    bus.add_signal_watch()
    bus.connect("message", _on_message, pipeline)

    _pipeline = pipeline
    _finished_cb = on_finished
    pipeline.set_state(Gst.State.PLAYING)


def stop():
    """Silence whatever is playing. No callback: this is the caller's own doing."""
    global _pipeline, _finished_cb
    if _pipeline is None:
        return
    pipeline, _pipeline, _finished_cb = _pipeline, None, None
    pipeline.get_bus().remove_signal_watch()
    pipeline.set_state(Gst.State.NULL)


def _on_message(_bus, message, pipeline):
    if pipeline is not _pipeline:
        # A pipeline already replaced or stopped; its messages are noise.
        return
    if message.type == Gst.MessageType.EOS:
        _finish(None)
    elif message.type == Gst.MessageType.ERROR:
        _finish("No se pudo reproducir el audio")


def _finish(error: str | None):
    callback = _finished_cb
    stop()
    if callback is not None:
        callback(error)
