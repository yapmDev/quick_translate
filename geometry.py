import gi
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk

# Fixed app-defined geometry: the MVP has no settings, so the panel is always
# centered and sized as a percentage of the primary monitor.
WIDTH_PERCENT = 32
HEIGHT_PERCENT = 45


def get_target_geometry() -> tuple[int, int, int, int]:
    display = Gdk.Display.get_default()
    monitor = display.get_primary_monitor() or display.get_monitor(0)
    geo = monitor.get_geometry()
    work = monitor.get_workarea()
    has_work = work.height > 0

    top = work.y if has_work else geo.y
    bottom_area_end = (work.y + work.height) if has_work else (geo.y + geo.height)

    w = int(geo.width * WIDTH_PERCENT / 100)
    h = int((work.height if has_work else geo.height) * HEIGHT_PERCENT / 100)

    x = geo.x + (geo.width - w) // 2
    y = top + (bottom_area_end - top - h) // 2

    return x, y, w, h


def apply_geometry(window, x: int, y: int, w: int, h: int):
    window.resize(w, h)
    window.move(x, y)
