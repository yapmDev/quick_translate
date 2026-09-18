import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk
import time

# Anything older than this was not copied "just now", so the panel opens with
# whatever it already had instead of pasting stale clipboard content.
MAX_AGE_S = 30.0

# When the clipboard last changed hands, as seen by this process. None means it
# has not changed since the app started — the age of what is in there now is
# unknowable, so it does not count as recent.
_changed_at: float | None = None


def _now() -> float:
    """A monotonic clock that keeps counting while the machine is suspended.

    CLOCK_MONOTONIC does not, so a copy made before a suspend comes back
    reading a few seconds old on resume and passes for fresh no matter how long
    the machine was away. BOOTTIME is the same clock with that time counted in.
    """
    return time.clock_gettime(time.CLOCK_BOOTTIME)


def start_tracking():
    """Begin stamping clipboard changes. Call once, at app start.

    The X server does hand out the time the current owner acquired the
    selection (the ICCCM TIMESTAMP target), but under XWayland that is useless:
    mutter's bridge advertises TIMESTAMP in TARGETS and then refuses to convert
    it, so every copy made from a Wayland app comes back undatable. Watching
    owner-change and stamping it here works for both kinds of source, at the
    cost of only knowing about copies made while the app was running — which is
    fine for a service that is always up.
    """
    Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).connect("owner-change", _on_owner_change)


def _on_owner_change(_clipboard, event):
    # NEW_OWNER means somebody copied. The other reasons fire when the owner
    # merely goes away (its window is destroyed or closed), which leaves the
    # same old content behind and must not pass for a fresh copy.
    if event.reason == Gdk.OwnerChange.NEW_OWNER:
        global _changed_at
        _changed_at = _now()


def request_fresh_text(callback, max_age: float = MAX_AGE_S):
    """Hand `callback` the clipboard text if it was copied less than `max_age`
    ago, or "" otherwise.

    Reading it is asynchronous — the owner is another process and has to be
    asked — so this returns immediately and answers later.
    """
    if _changed_at is None or _now() - _changed_at > max_age:
        callback("")
        return
    Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).request_text(_on_text, callback)


def _on_text(_clipboard, text, callback):
    callback(text if text and text.strip() else "")
