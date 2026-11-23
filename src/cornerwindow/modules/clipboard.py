"""Recent clipboard text; click an entry to copy it again."""

from __future__ import annotations

from gi.repository import Gtk, Pango

from ..core.activity import GREEN
from ..ui.widgets import IconButton, label
from .base import Module, module


@module("clipboard", title="Clipboard", area="drawer", icon="copy", description="Recent clipboard text, click to copy again")
class Clipboard(Module):
    def setup(self) -> None:
        self.history = self.ctx.services.clipboard
        self.history.on_change(self._render)

    def configure(self) -> None:
        self.history.size = int(self.config.get("size", 10))
        self.history.persist = bool(self.config.get("persist", False))

    def build(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        head = Gtk.Box()
        self.hint = label("Copied text shows up here", "cw-dim cw-small")
        head.pack_start(self.hint, True, True, 0)
        head.pack_end(IconButton("clear", self.history.clear, "Clear history", "cw-icon cw-plain", 14),
                      False, False, 0)
        outer.pack_start(head, False, False, 0)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        scroller.add(self.list)
        outer.pack_start(scroller, True, True, 0)
        self._render()
        return outer

    def _render(self) -> None:
        if not hasattr(self, "list"):
            return
        for child in self.list.get_children():
            self.list.remove(child)
        for text in self.history.items:
            one_line = " ".join(text.split())
            button = Gtk.Button()
            button.set_relief(Gtk.ReliefStyle.NONE)
            button.set_can_focus(False)
            button.get_style_context().add_class("cw-row")
            row = Gtk.Label(label=one_line[:300])
            row.set_xalign(0)
            row.set_ellipsize(Pango.EllipsizeMode.END)
            row.set_max_width_chars(1)
            button.add(row)
            button.set_tooltip_text(text[:500])
            button.connect("clicked", lambda _b, t=text: self._copy(t))
            self.list.pack_start(button, False, False, 0)
        self.hint.set_text(f"{len(self.history.items)} recent" if self.history.items else "Copied text shows up here")
        self.list.show_all()

    def _copy(self, text: str) -> None:
        self.history.copy(text)
        self.ctx.activity.show("clipboard", dot=True, color=GREEN, timeout=0.9)
        self.ctx.island.close()
