import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango
import threading
import prefs
import translate as translate_mod
import tts
from language_chooser import LanguageChooser

# Typing keeps firing "changed"; a translation is a network round trip, so the
# request only goes out once the user pauses. Long enough that a pause *inside*
# a sentence does not spend a request: Google's abuse system throttles this
# endpoint, and every keystroke gap under the threshold is one more request
# against whatever budget it is keeping.
DEBOUNCE_MS = 800

# Row 0 of the source selector. What Google detected is appended to it, so the
# detection is visible without spending toolbar width on a second label.
AUTO_LABEL = "Automatic"
# Longest language name is 21 characters and the auto row adds the detected one
# on top of that; past this the name ellipsizes instead of widening the toolbar.
LANG_WIDTH_CHARS = 21
# The selectors only offer the languages the user keeps (see prefs.py). The
# last row is the way out of that short list: it is not a language, it opens the
# full one — hence an id no language code can collide with.
SHOW_ALL = "__show_all__"
SHOW_ALL_LABEL = "Show all…"


class TranslateView(Gtk.Box):
    """Source box, target box and the language toolbar — the whole app, really."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        # The pair the panel was last left on, and the short list of languages
        # the selectors offer — both from the last run.
        self._source, self._target, self._favorites = prefs.load()
        # The language the last answer was detected as. Only meaningful while
        # the source is "auto", and the only thing that can turn that "auto"
        # into a concrete language for the swap button.
        self._detected: str | None = None
        # Raised while the selectors are moved from code: they fire "changed"
        # either way, and those handlers translate.
        self._syncing = False
        self._debounce_timeout: int | None = None
        # Responses arrive out of order (a short text typed after a long one can
        # come back first), so every result carries the id of the request that
        # asked for it and anything but the newest is dropped.
        self._request_id = 0
        # The same rule for the audio of a translation: the text can change
        # while its MP3 is still downloading.
        self._speech_id = 0

        self._build_ui()

    def _build_ui(self):
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        toolbar.set_name("toolbar")

        self.combo_source, self._source_store = self._build_lang_combo(
            "Language of the original text")
        self.combo_target, self._target_store = self._build_lang_combo(
            "Language of the translation")
        self._reload_lang_models()
        self.combo_source.connect("changed", self._on_source_lang_changed)
        self.combo_target.connect("changed", self._on_target_lang_changed)

        self.btn_swap = Gtk.Button()
        self.btn_swap.set_name("btn-swap")
        self.btn_swap.add(Gtk.Image.new_from_icon_name(
            "object-flip-horizontal-symbolic", Gtk.IconSize.SMALL_TOOLBAR))
        self.btn_swap.set_tooltip_text("Swap languages and texts")
        self.btn_swap.connect("clicked", self._on_swap)

        # Transient status/error messages only: the direction that was actually
        # used lives in the selectors themselves.
        self.status_label = Gtk.Label(label="", xalign=1)
        self.status_label.set_name("status-label")
        self.status_label.set_ellipsize(3)

        self.btn_clear = Gtk.Button()
        self.btn_clear.set_name("btn-action")
        self.btn_clear.add(Gtk.Image.new_from_icon_name(
            "edit-clear-all-symbolic", Gtk.IconSize.SMALL_TOOLBAR))
        self.btn_clear.set_tooltip_text("Clear")
        self.btn_clear.connect("clicked", self._on_clear)

        # Play/stop in one button: there is one translation on screen, so there
        # is never a second sound to choose between.
        self.btn_speak = Gtk.Button()
        self.btn_speak.set_name("btn-action")
        self._speak_icon = Gtk.Image.new_from_icon_name(
            "audio-volume-high-symbolic", Gtk.IconSize.SMALL_TOOLBAR)
        self.btn_speak.add(self._speak_icon)
        self.btn_speak.connect("clicked", self._on_speak)

        self.btn_copy = Gtk.Button()
        self.btn_copy.set_name("btn-action")
        self.btn_copy.add(Gtk.Image.new_from_icon_name(
            "edit-copy-symbolic", Gtk.IconSize.SMALL_TOOLBAR))
        self.btn_copy.set_tooltip_text("Copy translation")
        self.btn_copy.connect("clicked", self._on_copy)

        # Every action here is conditional (see _update_actions), so none of
        # them may be turned on by the panel's show_all: their visibility is
        # decided by the state of the boxes, not by the window coming up.
        # show_all stops at a no-show-all widget instead of descending into it,
        # so the icon inside each button has to be shown by hand — otherwise the
        # button comes up empty the moment _update_actions reveals it.
        for button in (self.btn_clear, self.btn_swap, self.btn_speak, self.btn_copy):
            button.set_no_show_all(True)
            button.get_child().show()

        # The languages sit dead center — set_center_widget is the only way to
        # center against the toolbar itself rather than against whatever space
        # the buttons happen to leave. Each action goes to the end it acts on:
        # clear with the source box, copy with the translation.
        languages = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        languages.pack_start(self.combo_source, False, False, 0)
        languages.pack_start(self.btn_swap, False, False, 0)
        languages.pack_start(self.combo_target, False, False, 0)

        toolbar.pack_start(self.btn_clear, False, False, 0)
        toolbar.set_center_widget(languages)
        # pack_end stacks right to left: copy takes the very end, listening
        # sits next to it (both act on the translation) and the status label
        # fills what is left between the selectors and them.
        toolbar.pack_end(self.btn_copy, False, False, 0)
        toolbar.pack_end(self.btn_speak, False, False, 0)
        toolbar.pack_end(self.status_label, True, True, 0)

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

        # Fixed 50/50 split with a plain rule in between. A box hands each child
        # its natural width before sharing out the rest, and a text view's
        # natural width follows its text — so without the size group the divider
        # would drift as the user types. With it both scrolls request exactly the
        # same, and the box can only split the remainder down the middle. The box
        # itself can't be homogeneous: the separator is a child too and would
        # claim a third of the width.
        same_width = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        same_width.add_widget(source_scroll)
        same_width.add_widget(target_scroll)

        panes = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        panes.set_name("panes")
        panes.pack_start(source_scroll, True, True, 0)
        panes.pack_start(
            Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 0)
        panes.pack_start(target_scroll, True, True, 0)

        self.pack_start(panes, True, True, 0)
        self.pack_start(toolbar, False, False, 0)

        # Reads both boxes, so it waits until they exist.
        self._sync_speak_button()
        self._update_actions()

    def _build_lang_combo(self, tooltip: str):
        """An empty selector over (code, name) rows, filled by _fill_store: the
        rows change as the short list does, so both models are kept around."""
        store = Gtk.ListStore(str, str)

        combo = Gtk.ComboBox.new_with_model(store)
        combo.set_name("lang-combo")
        combo.set_tooltip_text(tooltip)
        # Column 0 is the language code, so the selection is read and written as
        # a code (set_active_id/get_active_id) instead of as a row index. The
        # popup is a plain column: it only ever holds the short list, and the
        # hundred-language grid it used to need moved to the chooser dialog.
        combo.set_id_column(0)

        renderer = Gtk.CellRendererText(
            ellipsize=Pango.EllipsizeMode.END, max_width_chars=LANG_WIDTH_CHARS)
        combo.pack_start(renderer, True)
        combo.add_attribute(renderer, "text", 1)
        return combo, store

    def _visible_languages(self, current: str) -> list[tuple[str, str]]:
        """What a selector shows without being asked for the full list: the
        favourites, plus whatever it is currently set to — unstarring the
        selected language must not leave its own selector unable to show it.
        In LANGUAGES order, so starring never shuffles the rows around."""
        keep = set(self._favorites) | {current}
        return [entry for entry in translate_mod.LANGUAGES if entry[0] in keep]

    def _fill_store(self, store, current: str, auto_row: bool):
        store.clear()
        if auto_row:
            # Always row 0 of the source model: _set_detected relabels it there.
            store.append([translate_mod.AUTO, self._auto_label()])
        for code, name in self._visible_languages(current):
            store.append([code, name])
        store.append([SHOW_ALL, SHOW_ALL_LABEL])

    def _reload_lang_models(self):
        """Rebuild both short lists and put the selection back on them."""
        self._syncing = True
        self._fill_store(self._source_store, self._source, auto_row=True)
        self._fill_store(self._target_store, self._target, auto_row=False)
        self.combo_source.set_active_id(self._source)
        self.combo_target.set_active_id(self._target)
        self._syncing = False

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
        # Not debounced: the button follows the text on screen, not the
        # translation the text will eventually ask for.
        self._update_actions()
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
            self._set_translation("")
            self._set_status("")
            # There is nothing left to have detected. The languages themselves
            # stay put: they are a choice the user made in the selectors, not
            # something the box implies.
            self._set_detected(None)
            return False

        self._set_status("Translating…")
        threading.Thread(
            target=self._worker,
            args=(request_id, text, self._source, self._target),
            daemon=True,
        ).start()
        return False

    def _worker(self, request_id: int, text: str, source: str, target: str):
        try:
            result = translate_mod.translate(text, source=source, target=target)
        except translate_mod.TranslationError as exc:
            GLib.idle_add(self._on_failure, request_id, str(exc))
            return
        GLib.idle_add(self._on_success, request_id, result)

    def _on_success(self, request_id: int, result: dict):
        if request_id != self._request_id:
            return False
        self._set_detected(result["source"])

        if self._source == translate_mod.AUTO and result["source"] == self._target:
            # Detection landed on the target itself, so what came back is the
            # input again. "Auto" means "get me out of whatever this is", so the
            # panel turns around and asks for the alternate target — by moving
            # the selector, not by quietly translating somewhere else than what
            # the toolbar says. It can't loop: the new target is not the
            # detected language, so the next answer won't take this branch.
            self._select_languages(self._source, self._alternate_to(self._target))
            self.translate_now()
            return False

        self._set_translation(result["text"])
        self._set_status("")
        return False

    def _on_failure(self, request_id: int, message: str):
        if request_id != self._request_id:
            return False
        self._set_status(message, error=True)
        return False

    def _set_translation(self, text: str):
        self.target_view.get_buffer().set_text(text)
        # Whatever is playing belongs to the text that was there a moment ago,
        # so a new translation silences it: the box and the speakers must not
        # disagree. It also drops any audio still on its way.
        self.stop_audio()
        self._update_actions()

    def _set_status(self, text: str, error: bool = False):
        self.status_label.set_text(text)
        style = self.status_label.get_style_context()
        if error:
            style.add_class("status-error")
        else:
            style.remove_class("status-error")

    # ---- languages ---------------------------------------------------

    def _effective_source(self) -> str | None:
        """The language the source box is actually in — None while the panel is
        set to detect and nothing has come back yet."""
        if self._source != translate_mod.AUTO:
            return self._source
        return self._detected

    def _alternate_to(self, code: str) -> str:
        """Somewhere to translate into that isn't `code`."""
        if code != translate_mod.DEFAULT_TARGET:
            return translate_mod.DEFAULT_TARGET
        return translate_mod.ALTERNATE_TARGET

    def _auto_label(self) -> str:
        if self._detected:
            return f"{AUTO_LABEL} · {translate_mod.language_name(self._detected)}"
        return AUTO_LABEL

    def _set_detected(self, code: str | None):
        self._detected = code
        self._source_store[0][1] = self._auto_label()
        self._update_actions()

    def _select_languages(self, source: str, target: str):
        """Move the panel to a language pair, and remember it.

        Picking a language by hand is what puts it in the short list, so the
        models are refilled before the selection is applied to them — a language
        that is not in a selector yet cannot be selected in it.
        """
        self._source = source
        self._target = target
        for code in (source, target):
            if code != translate_mod.AUTO and code not in self._favorites:
                self._favorites.append(code)
        self._reload_lang_models()
        self._update_actions()
        prefs.save(self._source, self._target, self._favorites)

    def _update_actions(self):
        """Show each action only where it would do something.

        A button that is there but does nothing — clear on an empty box, copy
        on an empty translation — is a thing to read and decide about every
        time the panel opens, and a greyed-out one is the same cost without
        even the possibility of a click. The toolbar is small enough that the
        buttons appearing as they become usable reads as the panel following
        along rather than as things moving around.
        """
        self.btn_clear.set_visible(bool(self.get_source_text()))
        # Swapping needs a concrete language to put in the target selector, and
        # "auto" is only one once something has been detected.
        self.btn_swap.set_visible(self._effective_source() is not None)
        translation = bool(self.get_translation().strip())
        self.btn_copy.set_visible(translation)
        # Nothing to read out, or a target language Google has no voice for.
        self.btn_speak.set_visible(translation and tts.has_voice(self._target))

    def _picked(self, combo) -> str | None:
        """The language the user just chose in `combo`, or None if none was.

        The "show all" row is not a language: it puts the selector back where it
        was and hands over to the full chooser, whose answer takes its place.
        """
        code = combo.get_active_id()
        if code != SHOW_ALL:
            return code
        self._reload_lang_models()
        return self._open_chooser()

    def _open_chooser(self) -> str | None:
        parent = self.get_toplevel()
        chooser = LanguageChooser(
            parent if isinstance(parent, Gtk.Window) else None, self._favorites)
        code = chooser.pick()
        if list(chooser.favorites) != self._favorites:
            # Starring stands on its own: it has to be kept even when the dialog
            # was closed without picking a language.
            self._favorites = list(chooser.favorites)
            self._reload_lang_models()
            prefs.save(self._source, self._target, self._favorites)
        return code

    def _on_source_lang_changed(self, combo):
        if self._syncing:
            return
        code = self._picked(combo)
        if code is None or code == self._source:
            return
        target = self._target
        if code == target:
            # The same language on both sides would be asking Google for the
            # text back: the other selector moves out of the way.
            target = self._alternate_to(code)
        self._select_languages(code, target)
        # Whatever was detected belongs to the previous answer.
        self._set_detected(None)
        self.translate_now()

    def _on_target_lang_changed(self, combo):
        if self._syncing:
            return
        code = self._picked(combo)
        if code is None or code == self._target:
            return
        source = self._source
        if code == source:
            # Translating into the language the box is set to be in: the text is
            # what it is, so detection is the only sensible source from here.
            source = translate_mod.AUTO
            self._set_detected(None)
        self._select_languages(source, code)
        self.translate_now()

    # ---- toolbar actions ---------------------------------------------

    def _on_swap(self, _btn):
        """Turn the panel around: the translation becomes the text to translate.

        The new source text is in the language the last answer translated *to*,
        so that language becomes the source — no detection needed, and the
        result should read back roughly as what the user started from.
        """
        source = self._effective_source()
        if source is None:
            return
        translation = self.get_translation()
        previous_source = self.get_source_text()

        self._select_languages(self._target, source)
        self._set_detected(None)
        if translation:
            # The old source goes to the other side so the swap looks instant —
            # the real back-translation replaces it when it lands. Refilling the
            # box schedules a debounced translation; translate_now cancels that
            # timer and fires the new direction right away.
            self.set_source_text(translation)
            self._set_translation(previous_source)
        self.translate_now()

    def _on_clear(self, _btn):
        self.set_source_text("")
        self.focus_source()

    def _on_copy(self, _btn):
        if self.copy_translation():
            self._set_status("Translation copied")

    # ---- speech -------------------------------------------------------

    def stop_audio(self):
        """Silence the panel, and drop any audio still downloading."""
        self._speech_id += 1
        tts.stop()
        self._sync_speak_button()

    def _on_speak(self, _btn):
        if tts.is_playing():
            self.stop_audio()
            self._set_status("")
            return
        text = self.get_translation().strip()
        if not text:
            return
        self._speech_id += 1
        speech_id = self._speech_id
        self._set_status("Generating audio…")
        threading.Thread(
            target=self._speech_worker,
            args=(speech_id, text, self._target),
            daemon=True,
        ).start()

    def _speech_worker(self, speech_id: int, text: str, lang: str):
        try:
            data = tts.audio(text, lang)
        except tts.SpeechError as exc:
            GLib.idle_add(self._on_speech_failure, speech_id, str(exc))
            return
        GLib.idle_add(self._on_audio, speech_id, data)

    def _on_audio(self, speech_id: int, data: bytes):
        if speech_id != self._speech_id:
            # The translation moved on while this was downloading.
            return False
        self._set_status("")
        tts.play(data, self._on_playback_done)
        self._sync_speak_button()
        return False

    def _on_speech_failure(self, speech_id: int, message: str):
        if speech_id != self._speech_id:
            return False
        self._set_status(message, error=True)
        # A language that turned out to have no voice takes the button with it.
        self._update_actions()
        return False

    def _on_playback_done(self, error: str | None):
        if error:
            self._set_status(error, error=True)
        self._sync_speak_button()

    def _sync_speak_button(self):
        playing = tts.is_playing()
        self._speak_icon.set_from_icon_name(
            "media-playback-stop-symbolic" if playing else "audio-volume-high-symbolic",
            Gtk.IconSize.SMALL_TOOLBAR)
        self.btn_speak.set_tooltip_text("Stop" if playing else "Listen to the translation")
