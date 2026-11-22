"""Laptop panel brightness via /sys/class/backlight, written through logind (no root)."""

from __future__ import annotations

from pathlib import Path

from gi.repository import GLib

from ...core import log
from .. import dbus
from .base import BRIGHTNESS, Control, DisplayBackend, Monitor

_log = log.get("backlight")
SYS = Path("/sys/class/backlight")


class BacklightBackend(DisplayBackend):
    name = "backlight"

    def probe(self) -> list[Monitor]:
        monitors = []
        for device in sorted(SYS.glob("*")):
            try:
                maximum = int((device / "max_brightness").read_text())
                current = int((device / "brightness").read_text())
            except (OSError, ValueError):
                continue
            # Prefer firmware/platform interfaces over raw ones when several exist.
            monitors.append(Monitor(f"backlight:{device.name}", "Built-in display", "eDP", self,
                                    {BRIGHTNESS: Control(BRIGHTNESS, current, maximum)}))
        return monitors[:1]

    def write(self, monitor: Monitor, control: str, raw: int) -> None:
        device = monitor.id.split(":", 1)[1]
        try:
            dbus.call(dbus.system(), "org.freedesktop.login1", "/org/freedesktop/login1/session/auto",
                      "org.freedesktop.login1.Session", "SetBrightness",
                      GLib.Variant("(ssu)", ("backlight", device, max(raw, 1))))
        except GLib.Error:
            # Fall back to writing sysfs directly (works when the user has write access).
            (SYS / device / "brightness").write_text(str(max(raw, 1)))

    def read(self, monitor: Monitor, control: str) -> Control | None:
        device = SYS / monitor.id.split(":", 1)[1]
        return Control(BRIGHTNESS, int((device / "brightness").read_text()),
                       int((device / "max_brightness").read_text()))
