"""Lock / suspend / restart / power off, each behind a hold-to-confirm ring."""

from __future__ import annotations

from gi.repository import Gtk

from ..core import tasks
from ..platform import power
from ..ui.widgets import Card, HoldButton
from .base import Command, Module, module

ACTIONS = {
    "lock": ("lock", "Lock", power.lock, (0.6, 0.6, 0.65)),
    "suspend": ("suspend", "Sleep", power.suspend, (0.37, 0.36, 0.9)),
    "logout": ("logout", "Log out", power.logout, (1.0, 0.62, 0.04)),
    "reboot": ("reboot", "Restart", power.reboot, (1.0, 0.62, 0.04)),
    "poweroff": ("poweroff", "Shut down", power.poweroff, (1.0, 0.27, 0.23)),
}


@module("power", title="Power", icon="poweroff", description="Lock, sleep, restart and shut down")
class Power(Module):
    def configure(self) -> None:
        self.items = [i for i in self.config.get("items", ["lock", "suspend", "reboot", "poweroff"]) if i in ACTIONS]

    def _run(self, key: str) -> None:
        _icon, _title, fn, _color = ACTIONS[key]
        self.ctx.island.close()
        tasks.run_async(fn)

    def build(self):
        card = Card()
        row = Gtk.Box(spacing=0, homogeneous=True)
        hold = int(self.config.get("hold_ms", 600))
        for key in self.items:
            icon, title, _fn, color = ACTIONS[key]
            ms = int(self.config.get("lock_hold_ms", 250)) if key == "lock" else hold
            row.pack_start(HoldButton(icon, title, lambda k=key: self._run(k), ms, color), True, True, 0)
        card.pack_start(row, False, False, 0)
        return card

    def commands(self) -> list[Command]:
        # Lock and sleep run at once; the ones that close your apps need Enter twice in the bar,
        # the typed equivalent of the hold gesture.
        def runner(key: str):
            def run(_arg: str) -> str:
                self._run(key)
                return ACTIONS[key][1]
            return run

        return [
            Command(("lock",), "Lock screen", "lock", runner("lock"), icon="lock"),
            Command(("sleep", "suspend"), "Sleep", "sleep", runner("suspend"), icon="suspend"),
            Command(("logout", "signout"), "Log out", "logout", runner("logout"), confirm=True, icon="logout"),
            Command(("restart", "reboot"), "Restart", "restart", runner("reboot"), confirm=True, icon="reboot"),
            Command(("shutdown", "poweroff"), "Shut down", "shutdown", runner("poweroff"), confirm=True,
                    icon="poweroff"),
        ]
