"""Shelf: a place to park files, text and links while you move between windows. Drag something
onto the corner (the panel opens on the Shelf), drop it here, drag it out again later. Files stay
where they are (the shelf only remembers them); text and links are kept in memory."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse

from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from ..core.activity import GREEN
from ..platform.desktop import open_uri
from ..ui.widgets import IconButton, label
from ..ui.widgets.card import resolve_icon
from .base import Command, Module, Suggestion, module


@module("shelf", title="Shelf", area="drawer", icon="shelf",
        description="Park files, text and links: drop them on the corner, drag them out later")
class Shelf(Module):
    def setup(self) -> None:
        self.items: list[tuple[str, str]] = []  # ("uri", "file:///…") or ("text", "…")
        self.ctx.island.on_drag_hover.append(self._drag_over_corner)

    def _drag_over_corner(self) -> None:
        cfg = self.ctx.config
        if "shelf" not in (cfg.get("modules.drawer", []) or []) or "shelf" in (cfg.get("modules.hidden", []) or []):
            return
        self.ctx.island.open()
        drawer = self.ctx.host.instances.get("drawer")
        if drawer is not None and hasattr(drawer, "show"):
            GLib.idle_add(lambda: drawer.show("shelf") and False)

    def build(self):
        outer = Gtk.EventBox()
        outer.set_visible_window(False)
        outer.drag_dest_set(Gtk.DestDefaults.ALL, [], Gdk.DragAction.COPY)
        outer.drag_dest_add_uri_targets()
        outer.drag_dest_add_text_targets()
        outer.connect("drag-data-received", self._received)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        head = Gtk.Box()
        self.hint = label("", "cw-dim cw-small")
        head.pack_start(self.hint, True, True, 0)
        head.pack_end(IconButton("clear", self.clear, "Empty the shelf", "cw-icon cw-plain", 14), False, False, 0)
        box.pack_start(head, False, False, 0)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        scroller.add(self.list)
        box.pack_start(scroller, True, True, 0)
        outer.add(box)
        self._render()
        return outer

    def _received(self, _widget, _context, _x, _y, data, _info, _time) -> None:
        uris = data.get_uris()
        if uris:
            for uri in uris:
                self._add(("uri", uri))
        else:
            text = data.get_text()
            if text and text.strip():
                self._add(("text", text.strip()))
        self.ctx.activity.show("shelf", dot=True, color=GREEN, timeout=0.9)

    def _add(self, item: tuple[str, str]) -> None:
        if item in self.items:
            self.items.remove(item)
        self.items.insert(0, item)
        del self.items[int(self.config.get("size", 20)):]
        self._render()

    def _render(self) -> None:
        if not hasattr(self, "list"):
            return
        for child in self.list.get_children():
            self.list.remove(child)
            child.destroy()
        for item in self.items:
            self.list.pack_start(self._row(item), False, False, 0)
        self.hint.set_text(f"{len(self.items)} on the shelf · drag them out" if self.items
                           else "Drop files, text or links here, or on the corner")
        self.list.show_all()

    def _row(self, item: tuple[str, str]) -> Gtk.Widget:
        kind, value = item
        line = Gtk.Box(spacing=6)
        source = Gtk.EventBox()
        source.set_visible_window(False)
        inner = Gtk.Box(spacing=8)
        if kind == "uri":
            name = _uri_name(value)
            image = _uri_icon(value)
        else:
            name = " ".join(value.split())
            image = Gtk.Image.new_from_icon_name(resolve_icon("link" if "://" in value else "file"), Gtk.IconSize.MENU)
        image.set_pixel_size(18)
        inner.pack_start(image, False, False, 0)
        text = Gtk.Label(label=name[:200])
        text.set_xalign(0)
        text.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        text.set_max_width_chars(1)
        text.set_hexpand(True)
        inner.pack_start(text, True, True, 0)
        source.add(inner)
        source.set_tooltip_text(f"Drag it where you want it · double-click opens\n{unquote(value)[:300]}")
        source.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, [], Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        if kind == "uri":
            source.drag_source_add_uri_targets()
        else:
            source.drag_source_add_text_targets()
        source.connect("drag-data-get", lambda _w, _c, data, _i, _t, it=item: _fill(data, it))
        source.connect("button-press-event", lambda _w, e, it=item: self._pressed(e, it))
        line.pack_start(source, True, True, 0)
        line.pack_start(IconButton("close", lambda it=item: self._remove(it), "Take it off the shelf",
                                   "cw-icon cw-plain cw-mini", 10), False, False, 0)
        return line

    def _pressed(self, event, item) -> bool:
        if event.type == Gdk.EventType._2BUTTON_PRESS:
            self._open(item)
            return True
        return False

    def _open(self, item) -> str:
        kind, value = item
        if kind == "uri" or "://" in value:
            open_uri(value)
            self.ctx.island.close()
            return "Opened"
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(value, -1)
        return "Copied"

    def _remove(self, item) -> None:
        if item in self.items:
            self.items.remove(item)
        self._render()

    def clear(self) -> None:
        self.items.clear()
        self._render()

    def commands(self) -> list[Command]:
        def rows(arg: str) -> list[Suggestion]:
            found = [it for it in self.items if arg.lower() in unquote(it[1]).lower()]
            if not found:
                return [Suggestion("The shelf is empty" if not self.items else "Nothing on the shelf matches", "",
                                   lambda: None, "shelf")]
            return [Suggestion(_uri_name(v) if k == "uri" else " ".join(v.split())[:80],
                               "Enter opens" if k == "uri" or "://" in v else "Enter copies",
                               lambda it=(k, v): self._open(it), "shelf") for k, v in found[:7]]

        return [Command(("shelf",), "What's on the shelf", "shelf", lambda _a: None, True, suggest=rows)]


def _fill(data, item) -> None:
    kind, value = item
    if kind == "uri":
        data.set_uris([value])
    else:
        data.set_text(value, -1)


def _uri_name(uri: str) -> str:
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path)).name or uri
    return uri


def _uri_icon(uri: str) -> Gtk.Image:
    try:
        info = Gio.File.new_for_uri(uri).query_info("standard::icon", Gio.FileQueryInfoFlags.NONE, None)
        return Gtk.Image.new_from_gicon(info.get_icon(), Gtk.IconSize.MENU)
    except Exception:
        return Gtk.Image.new_from_icon_name(resolve_icon("file"), Gtk.IconSize.MENU)
