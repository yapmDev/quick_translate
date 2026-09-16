import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk


def add_classes(widget: Gtk.Widget, *classes: str) -> Gtk.Widget:
    """Put style classes on `widget` and hand it back, so a widget can be built
    and styled in one expression. The classes are base.css's `ds-` components;
    a widget keeps set_name() only for a layout rule of this app's own."""
    context = widget.get_style_context()
    for css_class in classes:
        context.add_class(css_class)
    return widget
