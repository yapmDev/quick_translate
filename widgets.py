import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib
import threading
import translate as translate_mod

# Typing keeps firing "changed"; a translation is a network round trip, so the
# request only goes out once the user pauses.
DEBOUNCE_MS = 450

# Direction cycles through the button: detect, or force one of the two.
MODES = ("auto", "en", "es")
MODE_LABELS = {"auto": "Auto", "en": "EN → ES", "es": "ES → EN"}


class TranslateView(Gtk.Box):
    """Source box, target box and a direction control — the whole app, really."""

    def __init__(self, hint: str = ""):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._hint = hint
        self._mode = "auto"
        self._debounce_timeout: int | None = None
        # Responses arrive out of order (a short text typed after a long one can
        # come back first), so every result carries the id of the request that
        # asked for it and anything but the newest is dropped.
        self._request_id = 0

        self._build_ui()
        self._set_status(self._hint)

    def _build_ui(self):
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        toolbar.set_name("toolbar")

        self.btn_direction = Gtk.Button(label=MODE_LABELS[self._mode])
        self.btn_direction.set_name("btn-direction")
        self.btn_direction.set_tooltip_text("Cambiar dirección")
        self.btn_direction.connect("clicked", self._on_cycle_direction)

        self.detected_label = Gtk.Label(label="", xalign=0)
        self.detected_label.set_name("detected-label")

        self.btn_clear = Gtk.Button()
        self.btn_clear.set_name("btn-action")
        self.btn_clear.add(Gtk.Image.new_from_icon_name(
            "edit-clear-symbolic", Gtk.IconSize.SMALL_TOOLBAR))
        self.btn_clear.set_tooltip_text("Limpiar")
        self.btn_clear.connect("clicked", self._on_clear)

        self.btn_copy = Gtk.Button()
        self.btn_copy.set_name("btn-action")
        self.btn_copy.add(Gtk.Image.new_from_icon_name(
            "edit-copy-symbolic", Gtk.IconSize.SMALL_TOOLBAR))
        self.btn_copy.set_tooltip_text("Copiar traducción")
        self.btn_copy.connect("clicked", self._on_copy)

        toolbar.pack_start(self.btn_direction, False, False, 0)
        toolbar.pack_start(self.detected_label, True, True, 0)
        toolbar.pack_end(self.btn_copy, False, False, 0)
        toolbar.pack_end(self.btn_clear, False, False, 0)

        source_scroll = Gtk.ScrolledWindow()
        source_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.source_view = Gtk.TextView()
        self.source_view.set_name("source-text")
        self.source_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.source_view.get_buffer().connect("changed", self._on_source_changed)
        source_scroll.add(self.source_view)

        target_scroll = Gtk.ScrolledWindow()
        target_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.target_view = Gtk.TextView()
        self.target_view.set_name("target-text")
        self.target_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.target_view.set_editable(False)
        self.target_view.set_cursor_visible(False)
        target_scroll.add(self.target_view)

        # Both halves share the space evenly and stay resizable by the user.
        panes = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        panes.set_name("panes")
        panes.pack1(source_scroll, True, False)
        panes.pack2(target_scroll, True, False)
        self._panes_split = False
        panes.connect("map", self._on_panes_map)

        self.status_label = Gtk.Label(label="", xalign=0)
        self.status_label.set_name("status-label")
        self.status_label.set_ellipsize(3)

        self.pack_start(toolbar, False, False, 0)
        self.pack_start(panes, True, True, 0)
        self.pack_start(self.status_label, False, False, 0)

    def _on_panes_map(self, panes):
        # Gtk.Paned only splits evenly if told to; without a position it hands
        # every extra pixel to the first child. Only on the first map — the
        # panel is mapped again on every show and that must not undo a split
        # the user dragged.
        if not self._panes_split:
            self._panes_split = True
            panes.set_position(panes.get_allocated_height() // 2)
        return False

    # ---- public API -------------------------------------------------

    def set_source_text(self, text: str):
        buf = self.source_view.get_buffer()
        buf.set_text(text)
        buf.place_cursor(buf.get_end_iter())

    def get_source_text(self) -> str:
        buf = self.source_view.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)

    def get_translation(self) -> str:
        buf = self.target_view.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)

    def copy_translation(self) -> bool:
        text = self.get_translation()
        if not text:
            return False
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)
        return True

    def focus_source(self):
        self.source_view.grab_focus()

    def translate_now(self):
        self._cancel_pending()
        self._run_translation()

    # ---- translation flow -------------------------------------------

    def _on_source_changed(self, _buf):
        self._cancel_pending()
        self._debounce_timeout = GLib.timeout_add(DEBOUNCE_MS, self._run_translation)

    def _cancel_pending(self):
        if self._debounce_timeout:
            GLib.source_remove(self._debounce_timeout)
            self._debounce_timeout = None

    def _run_translation(self):
        self._debounce_timeout = None
        text = self.get_source_text()
        # Bumped even for empty input: it invalidates whatever is still in
        # flight, so a late answer can't repopulate a box the user just cleared.
        self._request_id += 1
        request_id = self._request_id

        if not text.strip():
            self._set_target("")
            self.detected_label.set_text("")
            self._set_status(self._hint)
            return False

        self._set_status("Traduciendo…")
        threading.Thread(
            target=self._worker, args=(request_id, text, self._mode), daemon=True
        ).start()
        return False

    def _worker(self, request_id: int, text: str, mode: str):
        try:
            result = translate_mod.translate(text, source=mode)
        except translate_mod.TranslationError as exc:
            GLib.idle_add(self._on_failure, request_id, str(exc))
            return
        GLib.idle_add(self._on_success, request_id, result)

    def _on_success(self, request_id: int, result: dict):
        if request_id != self._request_id:
            return False
        self._set_target(result["text"])
        self.detected_label.set_text(f"{result['source']} → {result['target']}")
        self._set_status(self._hint)
        return False

    def _on_failure(self, request_id: int, message: str):
        if request_id != self._request_id:
            return False
        self._set_status(message, error=True)
        return False

    def _set_target(self, text: str):
        self.target_view.get_buffer().set_text(text)

    def _set_status(self, text: str, error: bool = False):
        self.status_label.set_text(text)
        style = self.status_label.get_style_context()
        if error:
            style.add_class("status-error")
        else:
            style.remove_class("status-error")

    # ---- toolbar actions ---------------------------------------------

    def _on_cycle_direction(self, _btn):
        self._mode = MODES[(MODES.index(self._mode) + 1) % len(MODES)]
        self.btn_direction.set_label(MODE_LABELS[self._mode])
        self.translate_now()

    def _on_clear(self, _btn):
        self.set_source_text("")
        self.focus_source()

    def _on_copy(self, _btn):
        if self.copy_translation():
            self._set_status("Traducción copiada")
