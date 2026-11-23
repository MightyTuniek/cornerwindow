"""Software updates: "12 updates · 3 Flatpak", and a button that opens the desktop's updater.
Hidden while everything is up to date. Checked when the panel opens, at most every
`check_hours`, on a worker thread (apt's count needs no network)."""

from __future__ import annotations

import time

from gi.repository import Gtk

from ..core import tasks
from ..core.activity import BLUE
from ..platform import proc, updates
from ..ui.widgets import Card, chip, label
from .base import Command, Module, module


@module("updates", title="Updates", icon="update", description="Available system and Flatpak updates; hidden when up to date")
class UpdatesCard(Module):
    def setup(self) -> None:
        self.found = updates.Updates()
        self.checked_at = 0.0
        self.checking = False

    def build(self):
        self.revealer = Gtk.Revealer()
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.revealer.set_transition_duration(240)
        card = Card(spacing=8, orientation=Gtk.Orientation.HORIZONTAL)
        image = Gtk.Image.new_from_icon_name("software-update-available-symbolic", Gtk.IconSize.MENU)
        image.set_pixel_size(22)
        card.pack_start(image, False, False, 0)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.title = label("", "cw-strong")
        self.detail = label("", "cw-dim cw-small", ellipsize=True)
        texts.pack_start(self.title, False, False, 0)
        texts.pack_start(self.detail, False, False, 0)
        card.pack_start(texts, True, True, 0)
        install = chip("Update", self.open_updater, "Open the software updater")
        install.set_valign(Gtk.Align.CENTER)
        card.pack_end(install, False, False, 0)
        self.revealer.add(card)
        self._render()
        return self.revealer

    def _render(self) -> None:
        if not hasattr(self, "title"):
            return
        total = self.found.total
        self.title.set_text(f"{total} update{'s' if total != 1 else ''}")
        self.detail.set_text(self.found.summary())
        self.revealer.set_reveal_child(total > 0)

    def on_open(self) -> None:
        self.check()

    def check(self, force: bool = False) -> None:
        hours = float(self.config.get("check_hours", 6))
        if self.checking or (not force and time.monotonic() - self.checked_at < hours * 3600 and self.checked_at):
            return
        self.checking = True

        def done(found: updates.Updates) -> None:
            self.checking = False
            self.checked_at = time.monotonic()
            self.found = found
            if found.total and self.config.get("trigger_dot", False):
                self.ctx.activity.show("updates", dot=True, color=BLUE, persistent=True, priority=0)
            else:
                self.ctx.activity.clear("updates")
            self._render()

        tasks.run_async(lambda: updates.check(bool(self.config.get("flatpak", True)),
                                              bool(self.config.get("snap", True))),
                        done, error=lambda _e: setattr(self, "checking", False))

    def open_updater(self) -> str:
        command = updates.updater()
        if command is None:
            return "No software updater found"
        proc.spawn(command)
        self.ctx.island.close()
        return "Opening the updater"

    def commands(self) -> list[Command]:
        def run(_arg: str) -> str:
            if self.found.total:
                return self.open_updater()
            self.check(force=True)
            return "Checking for updates…"

        return [Command(("updates", "upgrade"), "Software updates", "updates", run)]
