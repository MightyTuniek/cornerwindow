"""Countdown timer. While running, a ring on the collapsed trigger shows what's left."""

from __future__ import annotations

import time

from gi.repository import GLib, Gtk

from ..core.activity import ORANGE
from ..platform import proc
from ..ui.widgets import IconButton, chip, label
from .base import Command, Module, module

SOUND = "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga"


@module("timer", title="Timer", area="drawer", icon="timer")
class Timer(Module):
    def setup(self) -> None:
        self.duration = 0.0
        self.ends_at: float | None = None
        self.paused_left: float | None = None
        self._tick_id = 0
        self.ctx.actions.register("timer.toggle", self.toggle_pause, "Pause / resume timer")

    # -- logic ----------------------------------------------------------------------------
    def left(self) -> float:
        if self.paused_left is not None:
            return self.paused_left
        if self.ends_at is None:
            return 0.0
        return max(self.ends_at - time.monotonic(), 0.0)

    def start(self, seconds: float) -> str:
        self.duration = seconds
        self.ends_at = time.monotonic() + seconds
        self.paused_left = None
        self._ensure_tick()
        self._update()
        return f"Timer {_fmt(seconds)}"

    def add(self, seconds: float) -> None:
        if self.ends_at is None and self.paused_left is None:
            self.start(seconds)
            return
        self.duration += seconds
        if self.paused_left is not None:
            self.paused_left += seconds
        else:
            self.ends_at += seconds
        self._update()

    def toggle_pause(self) -> str:
        if self.paused_left is not None:
            self.ends_at = time.monotonic() + self.paused_left
            self.paused_left = None
            self._ensure_tick()
            self._update()
            return "Timer resumed"
        if self.ends_at is not None:
            self.paused_left = self.left()
            self._update()
            return "Timer paused"
        return "No timer running"

    def cancel(self) -> None:
        self.ends_at = self.paused_left = None
        self.duration = 0
        self.ctx.activity.clear("timer")
        self._update()

    def _ensure_tick(self) -> None:
        if not self._tick_id:
            self._tick_id = GLib.timeout_add(250, self._tick)

    def _tick(self) -> bool:
        if self.ends_at is None or self.paused_left is not None:
            self._tick_id = 0
            self._update()
            return False
        if self.left() <= 0:
            self._tick_id = 0
            self._finished()
            return False
        self._update()
        return True

    def _finished(self) -> None:
        total = self.duration
        self.ends_at = None
        self.duration = 0
        self.ctx.activity.clear("timer")
        self.ctx.activity.show("timer.done", dot=True, label="0:00", color=ORANGE, timeout=6, radius=40)
        self.ctx.notify("Timer finished", f"{_fmt(total)} is up", "alarm-symbolic")
        if proc.which("canberra-gtk-play"):
            proc.spawn(["canberra-gtk-play", "-i", "alarm-clock-elapsed"])
        elif proc.which("pw-play"):
            proc.spawn(["pw-play", SOUND])
        self._update()

    def _update(self) -> None:
        left = self.left()
        running = self.ends_at is not None or self.paused_left is not None
        if running and self.duration > 0:
            self.ctx.activity.show("timer", level=left / self.duration, color=ORANGE, persistent=True,
                                   priority=4, radius=30)
        if hasattr(self, "display"):
            self.display.set_text(_fmt(left) if running else "0:00")
            self.display.set_opacity(0.55 if self.paused_left is not None else 1.0)
            self.pause.set_icon("play" if self.paused_left is not None else "pause")
            self.controls.set_visible(running)

    # -- UI -------------------------------------------------------------------------------
    def build(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        top = Gtk.Box(spacing=8)
        self.display = label("0:00", "cw-timer")
        top.pack_start(self.display, True, True, 0)
        self.controls = Gtk.Box(spacing=6)
        self.controls.set_valign(Gtk.Align.CENTER)
        self.controls.pack_start(chip("+1 min", lambda: self.add(60)), False, False, 0)
        self.pause = IconButton("pause", self.toggle_pause, "Pause / resume", "cw-icon cw-big")
        self.controls.pack_start(self.pause, False, False, 0)
        self.controls.pack_start(IconButton("clear", self.cancel, "Cancel", "cw-icon cw-big"), False, False, 0)
        self.controls.set_no_show_all(True)
        for child in self.controls.get_children():
            child.show_all()
        top.pack_start(self.controls, False, False, 0)
        box.pack_start(top, False, False, 0)
        presets = Gtk.FlowBox()
        presets.set_selection_mode(Gtk.SelectionMode.NONE)
        presets.set_column_spacing(6)
        presets.set_row_spacing(6)
        for minutes in self.config.get("presets", [1, 5, 15, 25, 45]):
            presets.add(chip(f"{minutes} min", lambda m=minutes: self.start(m * 60)))
        box.pack_start(presets, False, False, 0)
        box.pack_start(label("Tip: type “timer 10” or “t 90s”", "cw-dim cw-small"), False, False, 0)
        self._update()
        return box

    def commands(self) -> list[Command]:
        def run(arg: str) -> str:
            arg = arg.strip().lower()
            if not arg:
                return self.toggle_pause()
            if arg in ("stop", "cancel"):
                self.cancel()
                return "Timer cancelled"
            seconds = _parse_duration(arg)
            if seconds is None:
                return "Usage: timer 10 · timer 90s · timer 1h"
            return self.start(seconds)

        return [Command(("timer", "t"), "Timer", "timer 10 (minutes)", run, True)]


def _parse_duration(text: str) -> float | None:
    total = 0.0
    number = ""
    found = False
    for ch in text.replace(" ", "") + " ":
        if ch.isdigit() or ch == ".":
            number += ch
            continue
        if not number:
            if ch == " ":
                break
            return None
        value = float(number)
        unit = {"h": 3600, "m": 60, "s": 1, " ": 60}.get(ch)
        if unit is None:
            return None
        total += value * unit
        number = ""
        found = True
    return total if found and total > 0 else None


def _fmt(seconds: float) -> str:
    seconds = int(round(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"
