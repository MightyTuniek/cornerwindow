"""Start typing anywhere in the open island: commands from every module + a calculator."""

from __future__ import annotations

from gi.repository import Gdk, GLib, Gtk, Pango

from ..core import calc
from ..ui.widgets import label
from ..ui.widgets.card import set_active
from .base import Command, Module, module

MAX_RESULTS = 5


@module("commandbar", title="Command bar", icon="search")
class CommandBar(Module):
    def setup(self) -> None:
        self.ctx.island.on_type = self.activate
        self.ctx.island.on_close_hooks.append(self.reset)
        self.ctx.island.on_escape.append(self._escape)
        self._matches: list[tuple[Command, str]] = []
        self._selected = 0
        self._feedback_id = 0

    def build(self):
        self.revealer = Gtk.Revealer()
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.revealer.set_transition_duration(200)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_bottom(10)
        corner = self.ctx.island.corner
        if corner.endswith("right"):
            box.set_margin_end(30)
        else:
            box.set_margin_start(30)
        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Type a command or a sum…  (b 40, v 20, timer 5, 2*21)")
        self.entry.connect("changed", self._changed)
        self.entry.connect("activate", self._run_selected)
        self.entry.connect("key-press-event", self._key)
        box.pack_start(self.entry, False, False, 0)
        self.answer = label("", "cw-strong")
        self.answer.set_margin_start(12)
        self.answer.set_no_show_all(True)
        box.pack_start(self.answer, False, False, 0)
        self.results = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        box.pack_start(self.results, False, False, 0)
        self.revealer.add(box)
        return self.revealer

    # -- island hooks -------------------------------------------------------------------
    def activate(self, first_char: str) -> bool:
        if not hasattr(self, "entry"):
            return False
        self.revealer.set_reveal_child(True)
        self.entry.grab_focus()
        self.entry.set_text(first_char)
        self.entry.set_position(-1)
        return True

    def _escape(self) -> bool:
        if hasattr(self, "revealer") and self.revealer.get_reveal_child():
            self.reset()
            self.ctx.island.set_focus(None)
            return True
        return False

    def reset(self) -> None:
        if hasattr(self, "entry"):
            self.entry.set_text("")
            self.revealer.set_reveal_child(False)
            self.answer.hide()

    # -- matching -----------------------------------------------------------------------
    def _changed(self, entry) -> None:
        text = entry.get_text()
        self._matches = []
        self._selected = 0
        self.answer.hide()
        if not text.strip():
            self._render()
            return
        if calc.looks_like_math(text):
            try:
                result = calc.evaluate(text)
                self.answer.set_text(f"= {calc.format_number(result)}")
                self.answer.show()
            except (ValueError, SyntaxError, ZeroDivisionError, TypeError, OverflowError):
                pass
        word, _, arg = text.strip().partition(" ")
        for command in self.ctx.host.commands():
            if command.matches(word):
                exact = word.lower() in command.keywords
                self._matches.append((command, arg, exact))
        self._matches.sort(key=lambda m: (not m[2], len(m[0].keywords[0])))
        self._matches = [(c, a) for c, a, _e in self._matches[:MAX_RESULTS]]
        self._render()

    def _render(self) -> None:
        for child in self.results.get_children():
            self.results.remove(child)
        for index, (command, arg) in enumerate(self._matches):
            button = Gtk.Button()
            button.set_relief(Gtk.ReliefStyle.NONE)
            button.set_can_focus(False)
            button.get_style_context().add_class("cw-row")
            row = Gtk.Box(spacing=8)
            title = Gtk.Label(label=command.title + (f"  {arg}" if arg and command.takes_arg else ""))
            title.set_xalign(0)
            title.set_ellipsize(Pango.EllipsizeMode.END)
            row.pack_start(title, True, True, 0)
            row.pack_end(label(command.usage, "cw-dim cw-small", xalign=1.0), False, False, 0)
            button.add(row)
            button.connect("clicked", lambda _b, i=index: self._run(i))
            set_active(button, index == self._selected)
            self.results.pack_start(button, False, False, 0)
        self.results.show_all()

    def _key(self, _entry, event) -> bool:
        if event.keyval in (Gdk.KEY_Down, Gdk.KEY_Tab) and self._matches:
            self._selected = (self._selected + 1) % len(self._matches)
            self._render()
            return True
        if event.keyval == Gdk.KEY_Up and self._matches:
            self._selected = (self._selected - 1) % len(self._matches)
            self._render()
            return True
        if event.keyval == Gdk.KEY_BackSpace and not self.entry.get_text():
            self.reset()
            return True
        return False

    def _run_selected(self, _entry) -> None:
        if self._matches:
            self._run(self._selected)
        elif self.answer.get_visible():
            # Enter on a sum copies the answer
            value = self.answer.get_text().lstrip("= ").replace(" ", "")
            Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(value, -1)
            self._feedback(f"Copied {value}")

    def _run(self, index: int) -> None:
        command, arg = self._matches[index]
        try:
            feedback = command.run(arg)
        except Exception as exc:
            feedback = f"Failed: {exc}"
        if not self.ctx.island.is_open:
            return
        self.entry.set_text("")
        self._feedback(feedback or command.title)

    def _feedback(self, text: str) -> None:
        self.answer.set_text(text)
        self.answer.show()
        if self._feedback_id:
            GLib.source_remove(self._feedback_id)

        def hide() -> bool:
            self._feedback_id = 0
            if not self.entry.get_text():
                self.answer.hide()
            return False

        self._feedback_id = GLib.timeout_add(1800, hide)
