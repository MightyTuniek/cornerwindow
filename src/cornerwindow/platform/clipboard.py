"""Clipboard history: text, plus the last few images (in memory only), and pinned entries that
stay until unpinned. Text is kept in memory unless the user opts into persistence; pins are
always saved. Optionally clears the system clipboard a while after you copy (passwords)."""

from __future__ import annotations

import json
from typing import Callable

from gi.repository import Gdk, GLib, Gtk

from ..core import log, paths

_log = log.get("clipboard")
MAX_ITEM_CHARS = 20000


class ClipboardHistory:
    def __init__(self, size: int = 10, persist: bool = False) -> None:
        self.size = size
        self.persist = persist
        self.items: list[str] = []
        self.images: list = []  # GdkPixbuf.Pixbuf, newest first
        self.image_count = 4
        self.clear_after = 0.0  # seconds; 0 = never clear the system clipboard
        self._clear_id = 0
        self._own_text: str | None = None
        self._listeners: list[Callable[[], None]] = []
        self._file = paths.data_dir() / "clipboard.json"
        self._pins_file = paths.data_dir() / "clipboard-pins.json"
        try:
            self.pinned: list[str] = [str(t) for t in json.loads(self._pins_file.read_text())]
        except (OSError, ValueError):
            self.pinned = []
        if persist:
            try:
                self.items = json.loads(self._file.read_text())[: size]
            except (OSError, ValueError):
                pass
        self._clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        self._clip.connect("owner-change", self._owner_change)

    def on_change(self, callback: Callable[[], None]) -> None:
        self._listeners.append(callback)

    def _owner_change(self, clipboard, _event) -> None:
        clipboard.request_targets(self._targets)

    def _targets(self, clipboard, atoms, *_rest) -> None:
        names = {a.name() for a in (atoms or []) if a is not None}
        has_text = any(n in names for n in ("UTF8_STRING", "text/plain", "text/plain;charset=utf-8", "STRING", "TEXT"))
        if has_text or not names:
            clipboard.request_text(self._received)
        elif self.image_count > 0 and any(n.startswith("image/") for n in names):
            clipboard.request_image(self._received_image)

    def _received(self, _clipboard, text: str | None) -> None:
        if not text or not text.strip() or len(text) > MAX_ITEM_CHARS:
            return
        own = text == self._own_text
        if own:
            self._own_text = None
        if text in self.items:
            self.items.remove(text)
        self.items.insert(0, text)
        del self.items[self.size:]
        self._save()
        if not own:
            self._schedule_clear()
        self._changed()

    def _received_image(self, _clipboard, pixbuf) -> None:
        if pixbuf is None:
            return
        if self.images and _same_image(self.images[0], pixbuf):
            return
        self.images.insert(0, pixbuf)
        del self.images[self.image_count:]
        self._schedule_clear()
        self._changed()

    def _changed(self) -> None:
        for callback in self._listeners:
            callback()

    # -- clearing the system clipboard after a while (history keeps the entry) -------------
    def _schedule_clear(self) -> None:
        if self._clear_id:
            GLib.source_remove(self._clear_id)
            self._clear_id = 0
        if self.clear_after > 0:
            self._clear_id = GLib.timeout_add(int(self.clear_after * 1000), self._clear_system)

    def _clear_system(self) -> bool:
        self._clear_id = 0
        self._own_text = ""
        self._clip.set_text("", 0)
        return False

    def copy(self, text: str) -> None:
        self._own_text = text
        self._clip.set_text(text, -1)
        self._clip.store()

    def copy_image(self, pixbuf) -> None:
        self._clip.set_image(pixbuf)
        self._clip.store()

    def plain(self) -> str | None:
        """Paste as plain text: put the newest text back on the clipboard as text only (no
        formatting, no HTML). Returns the text, or None when there's none."""
        if not self.items:
            return None
        self.copy(self.items[0])
        return self.items[0]

    # -- pins -------------------------------------------------------------------------
    def pin(self, text: str, on: bool = True) -> None:
        self.pinned = [t for t in self.pinned if t != text]
        if on:
            self.pinned.insert(0, text)
        try:
            self._pins_file.parent.mkdir(parents=True, exist_ok=True)
            self._pins_file.write_text(json.dumps(self.pinned))
        except OSError as exc:
            _log.warning("Could not save pinned clipboard entries: %s", exc)
        self._changed()

    def is_pinned(self, text: str) -> bool:
        return text in self.pinned

    def unpinned(self) -> list[str]:
        return [t for t in self.items if t not in self.pinned]

    def clear(self) -> None:
        """Forget the history (pins stay)."""
        self.items.clear()
        self.images.clear()
        self._save()
        self._changed()

    def _save(self) -> None:
        if self.persist:
            try:
                self._file.write_text(json.dumps(self.items))
            except OSError as exc:
                _log.warning("Could not save clipboard history: %s", exc)


def _same_image(a, b) -> bool:
    try:
        return (a.get_width(), a.get_height()) == (b.get_width(), b.get_height()) and \
            a.get_pixels()[:4096] == b.get_pixels()[:4096]
    except Exception:
        return False
