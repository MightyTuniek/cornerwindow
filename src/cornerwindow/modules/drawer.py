"""A card with segmented tabs; each tab is another module with area = "drawer"."""

from __future__ import annotations

from gi.repository import Gtk

from ..ui.widgets import Card
from ..ui.widgets.card import set_active
from .base import Module, module


@module("drawer", title="Drawer", icon="drawer", description="Tabs for the drawer cards")
class Drawer(Module):
    def build(self):
        ids = self.ctx.config.get("modules.drawer", ["timer", "clipboard", "notes"])
        card = Card(spacing=10)
        visible = [m for m in ids if m not in set(self.ctx.config.get("modules.hidden", []) or [])]
        crowded = len(visible) > 4  # five tabs: natural widths and tighter padding, so the names fit
        segments = Gtk.Box(spacing=2, homogeneous=not crowded)
        segments.get_style_context().add_class("cw-segments")
        if crowded:
            segments.get_style_context().add_class("cw-crowded")
        segments.set_halign(Gtk.Align.START)
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_transition_duration(240)
        self.stack.set_vhomogeneous(True)
        self.stack.set_size_request(-1, int(self.config.get("height", 100)))
        self.buttons: dict[str, Gtk.Button] = {}
        hidden = set(self.ctx.config.get("modules.hidden", []) or [])
        for mid in ids:
            if mid in hidden:
                continue
            widget = self.ctx.host.build(mid)
            if widget is None:
                continue
            instance = self.ctx.host.get(mid)
            self.stack.add_named(widget, mid)
            button = Gtk.Button(label=instance.title)
            button.set_relief(Gtk.ReliefStyle.NONE)
            button.set_can_focus(False)
            button.connect("clicked", lambda _b, m=mid: self.show(m))
            segments.pack_start(button, True, True, 0)
            self.buttons[mid] = button
        if not self.buttons:
            return None
        card.pack_start(segments, False, False, 0)
        card.pack_start(self.stack, True, True, 0)
        first = self.ctx.state.get("drawer.tab")
        self.show(first if first in self.buttons else next(iter(self.buttons)), animate=False)
        return card

    def on_open(self) -> None:
        """Open on whatever is running (a timer counting down, the stopwatch) so it's in view."""
        if not getattr(self, "buttons", None):
            return
        for mid in self.buttons:
            instance = self.ctx.host.instances.get(mid)
            busy = getattr(instance, "busy", None)
            if callable(busy) and busy():
                if self.stack.get_visible_child_name() != mid:
                    self.show(mid, animate=False)
                return

    def show(self, mid: str, animate: bool = True) -> None:
        if not animate:
            self.stack.set_visible_child_full(mid, Gtk.StackTransitionType.NONE)
        else:
            self.stack.set_visible_child_name(mid)
        for key, button in self.buttons.items():
            set_active(button, key == mid)
        self.ctx.state.set("drawer.tab", mid)
