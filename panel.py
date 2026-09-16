import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
from gi.repository import Gtk, Gdk, GdkX11, GLib
from pathlib import Path
import time
import clipboard as clipboard_mod
import geometry as geometry_mod
from design import add_classes
from widgets import TranslateView


class TranslatorPanel(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_name("panel-root")
        add_classes(self, "ds-window")
        self.set_title("Quick Translate")
        self.set_decorated(True)
        self.set_resizable(True)
        self.set_type_hint(Gdk.WindowTypeHint.NORMAL)

        self._pending_position = False
        self._hidden_at: float = 0.0
        self._focus_lost_at: float = 0.0

        self._load_css()
        self.view = TranslateView()
        self.add(self.view)
        self._position_panel()

        self.connect("key-press-event", self._on_key_press)
        self.connect("map-event", self._on_map_event)
        self.connect("delete-event", self._on_delete_event)
        self.connect("focus-out-event", self._on_focus_out)

    def _load_css(self):
        # style.css @imports base.css, the design system, so one provider
        # carries both in a fixed cascade order.
        css_path = Path(__file__).parent / "style.css"
        provider = Gtk.CssProvider()
        provider.load_from_path(str(css_path))
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _position_panel(self):
        x, y, w, h = geometry_mod.get_target_geometry()
        geometry_mod.apply_geometry(self, x, y, w, h)
        self._pending_position = True

    def _on_map_event(self, _widget, _event):
        # Some window managers ignore the move/resize issued before the window
        # is fully mapped, so it is applied a second time right after mapping.
        if self._pending_position:
            self._pending_position = False
            GLib.timeout_add(80, self._reapply_geometry)
        return False

    def _reapply_geometry(self):
        x, y, w, h = geometry_mod.get_target_geometry()
        geometry_mod.apply_geometry(self, x, y, w, h)
        return False

    def _on_key_press(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self._hide()
            return True
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            mods = event.state & Gtk.accelerator_get_default_mod_mask()
            if mods == Gdk.ModifierType.CONTROL_MASK:
                self.view.translate_now()
                return True
        return False

    def _on_delete_event(self, _widget, _event):
        self._hide()
        return True

    def _on_focus_out(self, _widget, _event):
        self._focus_lost_at = time.monotonic()
        return False

    def save_geometry(self):
        """Remember where the panel is right now, so the next show reuses it."""
        if not self.get_visible():
            return
        x, y = self.get_position()
        w, h = self.get_size()
        geometry_mod.save_last_geometry(x, y, w, h)

    def _hide(self):
        # Dismissing the panel is the moment its geometry becomes "the last one
        # the user chose" — every hide path goes through here. It is also the
        # only place audio has to be silenced: a panel that is gone must not
        # still be talking.
        self.view.stop_audio()
        self.save_geometry()
        self._hidden_at = time.monotonic()
        self.hide()

    def _request_focus(self):
        gdk_win = self.get_window()
        if gdk_win:
            try:
                ts = GdkX11.x11_get_server_time(gdk_win)
            except Exception:
                ts = Gdk.CURRENT_TIME
            gdk_win.focus(ts)
        self.view.focus_source()
        return False

    def _prefill_from_clipboard(self):
        clipboard_mod.request_fresh_text(self._on_fresh_clipboard)
        return False

    def _on_fresh_clipboard(self, text: str):
        # The answer comes from another process, so by now the panel may be
        # gone again or the user may already be typing that very text.
        if not text or not self.get_visible() or text == self.view.get_source_text():
            return
        self.view.set_source_text(text)
        self.view.translate_now()

    def _show(self):
        self._position_panel()
        self.show_all()
        self.present()
        GLib.idle_add(self._request_focus)
        # Opening the panel means "translate what I just copied" whenever there
        # is something recent enough to qualify.
        GLib.idle_add(self._prefill_from_clipboard)

    def summon(self):
        """Hotkey entry point: always come up, never toggle off.

        Re-triggering it re-reads the clipboard rather than hiding the panel —
        the hotkey is pressed right after copying something, so the intent is
        always "translate this", never "go away".
        """
        if not self.get_visible():
            self._show()
            return
        self.present()
        GLib.idle_add(self._request_focus)
        GLib.idle_add(self._prefill_from_clipboard)

    def toggle(self):
        if self.get_visible():
            # Without keep-above the panel stays visible but buried behind
            # whatever window took the focus, which reads as "hidden" — in that
            # case the toggle must raise it, not hide it. The focus-out caused
            # by the tray click itself lands a few ms before this call, so a
            # very recent focus loss still counts as "was on top". Raising is
            # not opening, so it does not touch what is already in the boxes.
            if not self.has_toplevel_focus() and time.monotonic() - self._focus_lost_at > 0.3:
                self.present()
                GLib.idle_add(self._request_focus)
                return
            self._hide()
        else:
            # A tray click that hides the panel must not immediately re-show it.
            if time.monotonic() - self._hidden_at < 0.3:
                return
            self._show()
