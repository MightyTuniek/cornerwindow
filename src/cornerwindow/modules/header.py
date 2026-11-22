"""Clock, date, pin and settings."""

from __future__ import annotations

import time

from gi.repository import Gtk

from .. import config as config_mod
from ..platform.desktop import open_uri
from ..ui.widgets import Card, IconButton, label
from ..ui.widgets.card import set_active
from .base import Module, module


@module("header", title="Clock")
class Header(Module):
    def build(self):
        card = Card(css="cw-flat", orientation=Gtk.Orientation.HORIZONTAL)
        right_corner = self.ctx.island.corner.endswith("right")
        # keep the very corner free: clicking it closes the island
        if right_corner:
            card.set_margin_end(26)
        else:
            card.set_margin_start(26)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.clock = label("", "cw-clock")
        self.date = label("", "cw-date")
        texts.pack_start(self.clock, False, False, 0)
        texts.pack_start(self.date, False, False, 0)
        buttons = Gtk.Box(spacing=6)
        buttons.set_valign(Gtk.Align.CENTER)
        self.pin = IconButton("pin", self.toggle_pin, "Keep open")
        buttons.pack_start(self.pin, False, False, 0)
        buttons.pack_start(IconButton("settings", self.open_settings, "Settings"), False, False, 0)
        if right_corner:
            card.pack_start(texts, True, True, 0)
            card.pack_end(buttons, False, False, 0)
        else:
            card.pack_start(buttons, False, False, 0)
            card.pack_end(texts, True, True, 0)
            self.clock.set_xalign(1.0)
            self.date.set_xalign(1.0)
        self.ctx.every(1.0, self.tick)
        self.tick()
        return card

    def tick(self) -> None:
        now = time.localtime()
        self.clock.set_text(time.strftime(self.config.get("clock_format", "%H:%M"), now))
        self.date.set_text(time.strftime(self.config.get("date_format", "%A, %-d %B"), now))

    def toggle_pin(self) -> None:
        pinned = not self.ctx.island.pinned
        self.ctx.island.set_pinned(pinned)
        set_active(self.pin, pinned)

    def on_close(self) -> None:
        set_active(self.pin, False)

    def open_settings(self) -> None:
        if self.ctx.actions.has("settings.toggle"):
            self.ctx.actions.run("settings.toggle")
        else:  # running without the app (tests): fall back to the file
            open_uri(config_mod.ensure_user_file().as_uri())
