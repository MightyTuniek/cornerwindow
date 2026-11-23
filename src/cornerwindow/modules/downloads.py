"""Downloads: what's coming in (with its size so far) and what just finished, with Open and
Show in folder. Hidden while there's nothing; a blue dot on the corner while downloading and a
green flash when a download finishes."""

from __future__ import annotations

from gi.repository import Gio, Gtk

from ..core.activity import BLUE, GREEN
from ..platform.desktop import open_uri
from ..platform.downloads import human_size
from ..ui.widgets import Card, IconButton, label
from ..ui.widgets.card import resolve_icon
from .base import Command, Module, Suggestion, module


@module("downloads", title="Downloads", icon="download",
        description="Downloads in progress and just finished; hidden while there are none")
class Downloads(Module):
    def setup(self) -> None:
        self.watcher = self.ctx.services.downloads
        self._active_before = 0
        self.ctx.bus.subscribe("downloads.changed", self._changed)

    def available(self) -> bool:
        return self.watcher.monitor is not None

    def build(self):
        self.revealer = Gtk.Revealer()
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.revealer.set_transition_duration(240)
        card = Card("Downloads", spacing=4)
        card.header.pack_end(IconButton("folder", self.open_folder, "Open the downloads folder",
                                        "cw-icon cw-plain cw-mini", 12), False, False, 0)
        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        card.pack_start(self.rows, False, False, 0)
        self.revealer.add(card)
        self._render()
        return self.revealer

    def _changed(self, **_kw) -> None:
        tracker = self.watcher.tracker
        active = len(tracker.active())
        if active:
            self.ctx.activity.show("downloads", dot=True, color=BLUE, persistent=True, priority=2)
        else:
            self.ctx.activity.clear("downloads")
            if self._active_before:
                self.ctx.activity.show("downloads.done", dot=True, color=GREEN, timeout=3)
        self._active_before = active
        if hasattr(self, "rows"):
            self._render()

    def _render(self) -> None:
        for child in self.rows.get_children():
            self.rows.remove(child)
            child.destroy()
        tracker = self.watcher.tracker
        tracker.prune()
        items = tracker.active() + tracker.recent()[: int(self.config.get("show", 3))]
        for item in items:
            self.rows.pack_start(self._row(item), False, False, 0)
        self.rows.show_all()
        self.revealer.set_reveal_child(bool(items))

    def _row(self, item) -> Gtk.Widget:
        line = Gtk.Box(spacing=8)
        image = _file_icon(item.path)
        line.pack_start(image, False, False, 0)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        texts.pack_start(label(item.name, "cw-small", ellipsize=True), False, False, 0)
        state = f"Downloading · {human_size(item.size)}" if item.finished is None else human_size(item.size)
        texts.pack_start(label(state, "cw-dim cw-small"), False, False, 0)
        line.pack_start(texts, True, True, 0)
        if item.finished is not None:
            line.pack_start(IconButton("file", lambda p=item.path: self._open(p), "Open",
                                       "cw-icon cw-plain cw-mini", 12), False, False, 0)
            line.pack_start(IconButton("folder", lambda p=item.path: self._reveal(p), "Show in folder",
                                       "cw-icon cw-plain cw-mini", 12), False, False, 0)
            line.pack_start(IconButton("close", lambda p=item.path: self._forget(p), "Remove from the list",
                                       "cw-icon cw-plain cw-mini", 10), False, False, 0)
        return line

    def _open(self, path) -> str:
        open_uri(path.as_uri())
        self.ctx.island.close()
        return f"Opened {path.name}"

    def _reveal(self, path) -> str:
        from ..platform import files

        reveal = getattr(files, "reveal", None)
        if callable(reveal):
            reveal(path)
        else:
            open_uri(path.parent.as_uri())
        self.ctx.island.close()
        return f"Showing {path.name}"

    def _forget(self, path) -> None:
        self.watcher.tracker.forget(path)
        self._render()

    def open_folder(self) -> None:
        open_uri(self.watcher.folder.as_uri())
        self.ctx.island.close()

    def commands(self) -> list[Command]:
        def suggest(arg: str) -> list[Suggestion]:
            tracker = self.watcher.tracker
            items = [d for d in tracker.active() + tracker.recent() if arg.lower() in d.name.lower()]
            rows = [Suggestion(d.name, "Downloading" if d.finished is None else f"{human_size(d.size)} · Enter opens",
                               (lambda p=d.path: self._open(p)) if d.finished is not None else (lambda: None),
                               "download", closes=d.finished is not None) for d in items[:6]]
            rows.append(Suggestion("Open the downloads folder", str(self.watcher.folder),
                                   lambda: self.open_folder() or "Opened", "folder", closes=True))
            return rows

        return [Command(("dl", "downloads"), "Recent downloads", "dl · dl invoice", lambda _a: self.open_folder(),
                        True, suggest=suggest)]


def _file_icon(path) -> Gtk.Image:
    try:
        content_type, _uncertain = Gio.content_type_guess(path.name, None)
        gicon = Gio.content_type_get_icon(content_type)
        image = Gtk.Image.new_from_gicon(gicon, Gtk.IconSize.MENU)
    except Exception:
        image = Gtk.Image.new_from_icon_name(resolve_icon("file"), Gtk.IconSize.MENU)
    image.set_pixel_size(20)
    return image
