"""Clipboard history (text only). Kept in memory unless the user opts into persistence."""

from __future__ import annotations

import json
from typing import Callable

from gi.repository import Gdk, Gtk

from ..core import log, paths

_log = log.get("clipboard")
MAX_ITEM_CHARS = 20000


class ClipboardHistory:
    def __init__(self, size: int = 10, persist: bool = False) -> None:
        self.size = size
        self.persist = persist
        self.items: list[str] = []
        self._own_text: str | None = None
        self._listeners: list[Callable[[], None]] = []
        self._file = paths.data_dir() / "clipboard.json"
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
        clipboard.request_text(self._received)

    def _received(self, _clipboard, text: str | None) -> None:
        if not text or not text.strip() or len(text) > MAX_ITEM_CHARS:
            return
        if text == self._own_text:
            self._own_text = None
        if text in self.items:
            self.items.remove(text)
        self.items.insert(0, text)
        del self.items[self.size:]
        self._save()
        for callback in self._listeners:
            callback()

    def copy(self, text: str) -> None:
        self._own_text = text
        self._clip.set_text(text, -1)
        self._clip.store()

    def clear(self) -> None:
        self.items.clear()
        self._save()
        for callback in self._listeners:
            callback()

    def _save(self) -> None:
        if self.persist:
            try:
                self._file.write_text(json.dumps(self.items))
            except OSError as exc:
                _log.warning("Could not save clipboard history: %s", exc)
