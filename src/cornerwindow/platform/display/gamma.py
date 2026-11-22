"""Software brightness via `xrandr --brightness` (X11 only). Last-resort fallback:
it dims the picture, not the backlight."""

from __future__ import annotations

import re

from .. import proc
from .base import BRIGHTNESS, Control, DisplayBackend, Monitor


class GammaBackend(DisplayBackend):
    name = "gamma"

    def probe(self) -> list[Monitor]:
        if not proc.which("xrandr"):
            return []
        out = proc.run(["xrandr", "--verbose"], timeout=5)
        monitors = []
        current_output = None
        for line in out.splitlines():
            head = re.match(r"^(\S+) connected", line)
            if head:
                current_output = head.group(1)
                continue
            bright = re.match(r"^\s+Brightness:\s+([\d.]+)", line)
            if bright and current_output:
                value = round(float(bright.group(1)) * 100)
                monitors.append(Monitor(f"gamma:{current_output}", current_output, current_output, self,
                                        {BRIGHTNESS: Control(BRIGHTNESS, value, 100, 10)}))
                current_output = None
        return monitors

    def write(self, monitor: Monitor, control: str, raw: int) -> None:
        output = monitor.id.split(":", 1)[1]
        proc.run(["xrandr", "--output", output, "--brightness", f"{max(raw, 10) / 100:.2f}"], timeout=3)

    def read(self, monitor: Monitor, control: str) -> Control | None:
        return monitor.controls.get(control)
