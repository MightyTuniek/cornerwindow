"""Stopwatch with laps. Keeps running while the panel is closed (a dot on the corner shows it);
the display only ticks while the panel is open."""

from __future__ import annotations

import time

from gi.repository import Gtk

from ..core.activity import GREEN
from ..ui.widgets import IconButton, chip, label
from .base import Command, Module, module


def fmt_elapsed(seconds: float) -> str:
    """1:02.3 under an hour, 1:02:03 above."""
    seconds = max(0.0, seconds)
    whole = int(seconds)
    h, rest = divmod(whole, 3600)
    m, s = divmod(rest, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}.{int((seconds - whole) * 10)}"


class Watch:
    """The stopwatch itself (no GTK), so it survives widget rebuilds and is easy to test."""

    def __init__(self, clock=time.monotonic) -> None:
        self.clock = clock
        self.started: float | None = None  # monotonic time of the last start
        self.banked = 0.0  # time from earlier runs (before the last pause)
        self.laps: list[float] = []  # total time at each lap

    @property
    def running(self) -> bool:
        return self.started is not None

    def elapsed(self) -> float:
        return self.banked + (self.clock() - self.started if self.started is not None else 0.0)

    def toggle(self) -> bool:
        if self.started is None:
            self.started = self.clock()
        else:
            self.banked = self.elapsed()
            self.started = None
        return self.running

    def lap(self) -> float | None:
        if not self.running:
            return None
        total = self.elapsed()
        self.laps.append(total)
        return total

    def reset(self) -> None:
        self.started, self.banked, self.laps = None, 0.0, []

    def splits(self) -> list[tuple[int, float, float]]:
        """(lap number, lap time, total), newest first."""
        out, previous = [], 0.0
        for i, total in enumerate(self.laps, 1):
            out.append((i, total - previous, total))
            previous = total
        return list(reversed(out))


@module("stopwatch", title="Stopwatch", area="drawer", icon="stopwatch", description="Stopwatch with laps")
class Stopwatch(Module):
    def setup(self) -> None:
        self.watch = Watch()
        self.ctx.actions.register("stopwatch.toggle", self.toggle, "Start / pause stopwatch")
        self.ctx.actions.register("stopwatch.lap", self.lap, "Stopwatch lap")

    # -- logic ----------------------------------------------------------------------------
    def toggle(self) -> str:
        running = self.watch.toggle()
        self._dot()
        self._update()
        return f"Stopwatch {'running' if running else 'paused at ' + fmt_elapsed(self.watch.elapsed())}"

    def lap(self) -> str:
        total = self.watch.lap()
        self._update(laps=True)
        return f"Lap {len(self.watch.laps)}: {fmt_elapsed(total)}" if total is not None else "Stopwatch isn't running"

    def reset(self) -> None:
        self.watch.reset()
        self._dot()
        self._update(laps=True)

    def _dot(self) -> None:
        if self.watch.running:
            self.ctx.activity.show("stopwatch", dot=True, color=GREEN, persistent=True, priority=2)
        else:
            self.ctx.activity.clear("stopwatch")

    # -- UI -------------------------------------------------------------------------------
    def build(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        top = Gtk.Box(spacing=8)
        self.display = label("0:00.0", "cw-timer cw-tnum")
        top.pack_start(self.display, True, True, 0)
        controls = Gtk.Box(spacing=6)
        controls.set_valign(Gtk.Align.CENTER)
        self.lap_button = chip("Lap", self.lap)
        controls.pack_start(self.lap_button, False, False, 0)
        self.play = IconButton("play", self.toggle, "Start / pause", "cw-icon cw-big")
        controls.pack_start(self.play, False, False, 0)
        controls.pack_start(IconButton("reset", self.reset, "Reset", "cw-icon cw-big"), False, False, 0)
        top.pack_start(controls, False, False, 0)
        box.pack_start(top, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        self.laps_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        scroll.add(self.laps_box)
        box.pack_start(scroll, True, True, 0)
        self.ctx.every(0.1, self._update)
        self._update(laps=True)
        return box

    def _update(self, laps: bool = False) -> None:
        if not hasattr(self, "display"):
            return
        self.display.set_text(fmt_elapsed(self.watch.elapsed()))
        self.play.set_icon("pause" if self.watch.running else "play")
        self.lap_button.set_sensitive(self.watch.running)
        if laps:
            for child in self.laps_box.get_children():
                self.laps_box.remove(child)
                child.destroy()
            splits = self.watch.splits()
            if not splits:
                self.laps_box.pack_start(label("Tip: type “sw” to start or pause", "cw-dim cw-small"),
                                         False, False, 0)
            for number, split, total in splits:
                row = Gtk.Box(spacing=8)
                row.pack_start(label(f"Lap {number}", "cw-dim cw-small"), False, False, 0)
                row.pack_start(label(fmt_elapsed(split), "cw-small cw-tnum", xalign=1.0), True, True, 0)
                row.pack_start(label(fmt_elapsed(total), "cw-dim cw-small cw-tnum", xalign=1.0), False, False, 0)
                self.laps_box.pack_start(row, False, False, 0)
            self.laps_box.show_all()

    def commands(self) -> list[Command]:
        return [
            Command(("sw", "stopwatch"), "Start / pause stopwatch", "sw", lambda _a: self.toggle()),
            Command(("lap",), "Stopwatch lap", "lap", lambda _a: self.lap()),
        ]
