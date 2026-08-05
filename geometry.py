import gi
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk

import json
from pathlib import Path

# App-defined geometry: the MVP has no settings, so the panel comes up centered
# and sized as a percentage of the primary monitor. This is only the *first*
# geometry — once the user moves or resizes the window, the last one wins.
WIDTH_PERCENT = 32
HEIGHT_PERCENT = 45

# Window geometry is the one thing the app remembers between runs: it is state
# the user set with the mouse, not a preference, so it lives next to the code
# that applies it instead of turning into a settings file.
STATE_DIR = Path.home() / ".config" / "quick_translate"
STATE_PATH = STATE_DIR / "geometry.json"


def load_last_geometry() -> tuple[int, int, int, int] | None:
    """Position and size the panel was last dismissed at, if it is usable."""
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        x, y, w, h = (int(data[key]) for key in ("x", "y", "width", "height"))
    except (KeyError, TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return x, y, w, h


def save_last_geometry(x: int, y: int, w: int, h: int):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(
            json.dumps({"x": x, "y": y, "width": w, "height": h}, indent=2),
            encoding="utf-8",
        )
    except OSError:
        # Not being able to remember where the panel was is never a reason to
        # fail the action that is closing it.
        pass


def get_target_geometry() -> tuple[int, int, int, int]:
    display = Gdk.Display.get_default()
    monitor = display.get_primary_monitor() or display.get_monitor(0)
    geo = monitor.get_geometry()
    work = monitor.get_workarea()
    area = work if work.height > 0 else geo

    saved = load_last_geometry()
    if saved:
        # The remembered geometry may come from a different monitor layout, so
        # it is pulled back inside the current work area before being reused.
        x, y, w, h = saved
        w = min(w, area.width)
        h = min(h, area.height)
        x = min(max(x, area.x), area.x + area.width - w)
        y = min(max(y, area.y), area.y + area.height - h)
        return x, y, w, h

    w = int(geo.width * WIDTH_PERCENT / 100)
    h = int(area.height * HEIGHT_PERCENT / 100)

    x = geo.x + (geo.width - w) // 2
    y = area.y + (area.height - h) // 2

    return x, y, w, h


def apply_geometry(window, x: int, y: int, w: int, h: int):
    window.resize(w, h)
    window.move(x, y)
