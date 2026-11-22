"""Cards, labels, chips and icon buttons (plain GTK widgets styled by style.css)."""

from __future__ import annotations

from typing import Callable

from gi.repository import Gtk, Pango

from ..icons import FALLBACKS


class Card(Gtk.Box):
    """A rounded translucent tile. `title` adds a small dim heading."""

    def __init__(self, title: str | None = None, spacing: int = 8,
                 orientation: Gtk.Orientation = Gtk.Orientation.VERTICAL, css: str = "") -> None:
        super().__init__(orientation=orientation, spacing=spacing)
        self.get_style_context().add_class("cw-card")
        if css:
            self.get_style_context().add_class(css)
        self.header: Gtk.Box | None = None
        if title:
            self.header = Gtk.Box(spacing=6)
            self.header.pack_start(label(title, "cw-title"), False, False, 0)
            self.pack_start(self.header, False, False, 0)


def label(text: str = "", css: str = "", xalign: float = 0.0, ellipsize: bool = False) -> Gtk.Label:
    widget = Gtk.Label(label=text)
    widget.set_xalign(xalign)
    if css:
        for cls in css.split():
            widget.get_style_context().add_class(cls)
    if ellipsize:
        widget.set_ellipsize(Pango.EllipsizeMode.END)
        widget.set_max_width_chars(1)
        widget.set_hexpand(True)
    return widget


def resolve_icon(name: str) -> str:
    theme = Gtk.IconTheme.get_default()
    for candidate in FALLBACKS.get(name, (name,)):
        if theme.has_icon(candidate):
            return candidate
    return FALLBACKS.get(name, (name,))[0]


class IconButton(Gtk.Button):
    def __init__(self, icon: str, on_click: Callable[[], None] | None = None, tooltip: str = "",
                 css: str = "cw-icon", size: int = 16) -> None:
        super().__init__()
        self.image = Gtk.Image.new_from_icon_name(resolve_icon(icon), Gtk.IconSize.MENU)
        self.image.set_pixel_size(size)
        self.add(self.image)
        self.set_relief(Gtk.ReliefStyle.NONE)
        self.set_can_focus(False)
        for cls in css.split():
            self.get_style_context().add_class(cls)
        if tooltip:
            self.set_tooltip_text(tooltip)
        if on_click:
            self.connect("clicked", lambda *_: on_click())

    def set_icon(self, icon: str) -> None:
        self.image.set_from_icon_name(resolve_icon(icon), Gtk.IconSize.MENU)


def chip(text: str, on_click: Callable[[], None] | None = None, tooltip: str = "") -> Gtk.Button:
    button = Gtk.Button(label=text)
    button.set_relief(Gtk.ReliefStyle.NONE)
    button.set_can_focus(False)
    button.get_style_context().add_class("cw-chip")
    if tooltip:
        button.set_tooltip_text(tooltip)
    if on_click:
        button.connect("clicked", lambda *_: on_click())
    return button


def set_active(widget: Gtk.Widget, active: bool) -> None:
    ctx = widget.get_style_context()
    if active:
        ctx.add_class("active")
    else:
        ctx.remove_class("active")
