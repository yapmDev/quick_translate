import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, Pango
import translate as translate_mod

STAR_ON = "starred-symbolic"
STAR_OFF = "non-starred-symbolic"


class LanguageChooser(Gtk.Dialog):
    """The full language list: search it, pick one, star the ones worth keeping.

    Modal over the panel, because the selector that opened it is waiting for an
    answer. `pick()` runs it and returns the chosen code (None if it was closed
    without picking); the favourites it was left with are read off `favorites`,
    and those survive a dialog that picked nothing — starring is a change of its
    own, not something the caller only gets as a side effect of a selection.
    """

    def __init__(self, parent, favorites):
        super().__init__(
            title="Languages", transient_for=parent, modal=True, destroy_with_parent=True)
        self.set_name("lang-chooser")
        self.set_default_size(300, 420)
        self.favorites = list(dict.fromkeys(favorites))
        self.chosen: str | None = None
        self._query = ""
        # Row -> code. The rows are only ever reached through this, which keeps
        # the mapping off the widgets and the rows' Python wrappers alive.
        self._codes: dict[Gtk.ListBoxRow, str] = {}
        self._build_ui()

    def _build_ui(self):
        self.search = Gtk.SearchEntry()
        self.search.set_name("lang-search")
        self.search.set_placeholder_text("Search language")
        self.search.connect("search-changed", self._on_search_changed)
        # Enter takes whatever the search narrowed the list down to, so a
        # language is three keystrokes away without touching the mouse.
        self.search.connect("activate", self._on_search_activate)

        self.listbox = Gtk.ListBox()
        self.listbox.set_name("lang-list")
        # Rows are activated, never selected: picking one closes the dialog, so
        # a selection would only ever be a highlight left behind.
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.listbox.set_filter_func(self._filter_row)
        self.listbox.connect("row-activated", self._on_row_activated)
        for code, name in translate_mod.LANGUAGES:
            self.listbox.add(self._build_row(code, name))

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self.listbox)

        body = self.get_content_area()
        body.set_name("lang-chooser-body")
        body.set_spacing(8)
        body.pack_start(self.search, False, False, 0)
        body.pack_start(scroll, True, True, 0)

        self.add_button("Close", Gtk.ResponseType.CLOSE)
        self.connect("key-press-event", self._on_key_press)

    def _build_row(self, code: str, name: str) -> Gtk.ListBoxRow:
        starred = code in self.favorites

        star = Gtk.ToggleButton()
        star.set_name("btn-star")
        star.set_relief(Gtk.ReliefStyle.NONE)
        star.add(Gtk.Image.new_from_icon_name(
            STAR_ON if starred else STAR_OFF, Gtk.IconSize.SMALL_TOOLBAR))
        star.set_active(starred)
        star.set_tooltip_text("Keep in the selector")
        star.connect("toggled", self._on_star_toggled, code)

        label = Gtk.Label(label=name, xalign=0)
        label.set_ellipsize(Pango.EllipsizeMode.END)

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        content.pack_start(label, True, True, 0)
        # The star is a button inside the row, so clicking it toggles the
        # favourite instead of activating the row it sits in.
        content.pack_end(star, False, False, 0)

        row = Gtk.ListBoxRow()
        row.set_name("lang-row")
        row.add(content)
        self._codes[row] = code
        return row

    # ---- public API -------------------------------------------------

    def pick(self) -> str | None:
        self.show_all()
        self.search.grab_focus()
        self.run()
        self.destroy()
        return self.chosen

    # ---- events -------------------------------------------------------

    def _on_key_press(self, _widget, event):
        # The toplevel sees the key before the focused widget does, which is the
        # whole point: Gtk.SearchEntry answers Escape by clearing its own text,
        # so without this Escape never reaches the dialog while the search box
        # has the focus it is given on open.
        if event.keyval == Gdk.KEY_Escape:
            self.response(Gtk.ResponseType.CLOSE)
            return True
        return False

    def _filter_row(self, row: Gtk.ListBoxRow) -> bool:
        return translate_mod.language_matches(self._codes[row], self._query)

    def _on_search_changed(self, entry):
        self._query = entry.get_text()
        self.listbox.invalidate_filter()

    def _on_search_activate(self, _entry):
        # The model is in display order, so the first match is the first row the
        # user is looking at.
        for row, code in self._codes.items():
            if translate_mod.language_matches(code, self._query):
                self._choose(code)
                return

    def _on_row_activated(self, _listbox, row):
        self._choose(self._codes[row])

    def _on_star_toggled(self, button, code: str):
        starred = button.get_active()
        if starred and code not in self.favorites:
            self.favorites.append(code)
        elif not starred and code in self.favorites:
            self.favorites.remove(code)
        button.get_child().set_from_icon_name(
            STAR_ON if starred else STAR_OFF, Gtk.IconSize.SMALL_TOOLBAR)

    def _choose(self, code: str):
        self.chosen = code
        self.response(Gtk.ResponseType.OK)
