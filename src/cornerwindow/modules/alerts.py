"""System alerts: a notification (and a dot on the corner) when the CPU stays busy, the machine
runs hot, memory or the disk fills up. Off by default: unlike everything else it checks in the
background while the panel is closed (one cheap read every `interval` seconds)."""

from __future__ import annotations

import time

from gi.repository import GLib

from ..core.activity import ORANGE, RED
from ..core.alerts import Rules
from ..platform import sensors
from .base import Command, Module, module


@module("alerts", title="Alerts", area="tool", icon="alert",
        description="Warns when the CPU stays busy, it runs hot, or memory or disk fill up (checks in the background)")
class Alerts(Module):
    def setup(self) -> None:
        self.cpu = sensors.CpuUsage()
        self._source = 0
        self.last: list[tuple[str, str, str]] = []

    def configure(self) -> None:
        self.rules = Rules.from_section(self.config)
        self.temp = sensors.find_sensor(self.ctx.config.get("glance.cpu_sensor", "auto"), "cpu")
        self.disk_path = str(self.ctx.config.get("glance.disk_path", "/"))
        cfg = self.ctx.config
        enabled = "alerts" in (cfg.get("modules.tools", []) or []) and "alerts" not in (cfg.get("modules.hidden", []) or [])
        if self._source and not enabled:  # switched off: stop checking
            GLib.source_remove(self._source)
            self._source = 0

    def build(self):
        if not self._source:
            self._source = GLib.timeout_add_seconds(max(5, int(self.config.get("interval", 15))), self._check)
        return None

    def _check(self) -> bool:
        def read():
            used, total = sensors.memory()
            free, _total = sensors.disk(self.disk_path)
            return (self.cpu.sample(), self.temp.read() if self.temp else None,
                    100 * used / max(total, 0.1), free)

        def done(values) -> None:
            cpu, temp, mem, free = values
            for key, title, body in self.rules.check(time.monotonic(), cpu, temp, mem, free):
                self.last.insert(0, (key, title, body))
                del self.last[10:]
                self.ctx.notify(title, body, "dialog-warning-symbolic")
                self.ctx.activity.show(f"alerts.{key}", dot=True, color=RED if key in ("temperature", "disk") else ORANGE,
                                       timeout=30)

        self.ctx.run_async(read, done)
        return True

    def commands(self) -> list[Command]:
        def run(_arg: str) -> str:
            if not self.last:
                return "No alerts"
            return " · ".join(f"{title} ({body})" for _k, title, body in self.last[:3])

        return [Command(("alerts",), "Recent system alerts", "alerts", run)]
