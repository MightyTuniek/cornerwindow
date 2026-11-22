"""A scratchpad that saves itself."""

from __future__ import annotations

from gi.repository import GLib, Gtk

from .base import Module, module


@module("notes", title="Notes", area="drawer", icon="notes")
class Notes(Module):
    def setup(self) -> None:
        self.path = self.ctx.data_dir / "notes.txt"
        self._save_id = 0

    def build(self):
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.get_style_context().add_class("cw-notes")
        self.view = Gtk.TextView()
        self.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.view.set_left_margin(2)
        self.view.set_right_margin(2)
        buffer = self.view.get_buffer()
        try:
            buffer.set_text(self.path.read_text())
        except OSError:
            buffer.set_text("")
        buffer.connect("changed", self._changed)
        scroller.add(self.view)
        return scroller

    def _changed(self, _buffer) -> None:
        if self._save_id:
            GLib.source_remove(self._save_id)
        self._save_id = GLib.timeout_add(600, self._save)

    def _save(self) -> bool:
        self._save_id = 0
        buffer = self.view.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        try:
            self.path.write_text(text)
        except OSError:
            pass
        return False

    def on_close(self) -> None:
        if self._save_id:
            GLib.source_remove(self._save_id)
            self._save()
