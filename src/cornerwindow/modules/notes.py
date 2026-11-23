"""A scratchpad that saves itself."""

from __future__ import annotations

from gi.repository import Gdk, GLib, Gtk

from .base import Command, Module, Suggestion, module


@module("notes", title="Notes", area="drawer", icon="notes", description="A scratchpad that saves itself")
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

    # -- command bar: "note buy milk" adds a line, "note milk" also finds lines ---------------
    def _text(self) -> str:
        if hasattr(self, "view"):
            buffer = self.view.get_buffer()
            return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        try:
            return self.path.read_text()
        except OSError:
            return ""

    def append(self, line: str) -> str:
        line = line.strip()
        if not line:
            return "Type something after “note”"
        text = self._text()
        addition = ("" if not text or text.endswith("\n") else "\n") + line + "\n"
        if hasattr(self, "view"):
            buffer = self.view.get_buffer()
            buffer.insert(buffer.get_end_iter(), addition)  # saves itself
        else:
            try:
                with self.path.open("a") as handle:
                    handle.write(addition)
            except OSError as exc:
                return f"Could not save: {exc}"
        return "Added to notes"

    def commands(self) -> list[Command]:
        def copy(line: str) -> str:
            Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(line, -1)
            return "Copied"

        def suggest(arg: str) -> list[Suggestion]:
            arg = arg.strip()
            rows = []
            if arg:
                rows.append(Suggestion(f"Add “{arg}” to notes", "Enter", lambda a=arg: self.append(a), "notes"))
            words = arg.lower().split()
            lines = [ln.strip() for ln in self._text().splitlines() if ln.strip()]
            found = [ln for ln in reversed(lines) if all(w in ln.lower() for w in words)]
            rows += [Suggestion(ln[:120], "Copy", lambda ln=ln: copy(ln), "copy") for ln in found[:6]]
            if not rows:
                rows.append(Suggestion("Notes are empty: type “note” and some text", "", lambda: None, "notes"))
            return rows

        return [Command(("note", "notes"), "Add to notes or find a line", "note buy milk · note milk",
                        self.append, True, suggest=suggest)]
