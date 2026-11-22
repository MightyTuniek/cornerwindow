"""DDC/CI through the ddcutil CLI. Slower than the native backend, but battle tested."""

from __future__ import annotations

import re

from ...core import log
from .. import proc
from .base import VCP_CODES, Control, DisplayBackend, DisplayError, Monitor

_log = log.get("ddcutil")
_FAST = ["--noverify", "--sleep-multiplier", "0.4"]


def parse_detect(text: str) -> list[tuple[int, str, str]]:
    """Parse `ddcutil detect --brief` into (bus, model, connector)."""
    displays = []
    for block in re.split(r"\n(?=Display \d+)", text):
        if not block.startswith("Display"):
            continue
        bus = re.search(r"/dev/i2c-(\d+)", block)
        model = re.search(r"Monitor:\s+[^:]*:([^:]*):", block)
        connector = re.search(r"DRM[_ ]connector:\s+card\d+-(\S+)", block)
        if bus:
            displays.append((int(bus.group(1)), model.group(1).strip() if model else f"i2c-{bus.group(1)}",
                             connector.group(1) if connector else ""))
    return displays


def parse_getvcp(text: str) -> tuple[int, int]:
    """Parse `ddcutil getvcp 10 --brief` -> 'VCP 10 C 50 100'."""
    match = re.search(r"VCP\s+[0-9A-Fa-f]+\s+C\s+(\d+)\s+(\d+)", text)
    if not match:
        raise DisplayError(f"unexpected ddcutil output: {text.strip()!r}")
    return int(match.group(1)), int(match.group(2))


class DdcutilBackend(DisplayBackend):
    name = "ddcutil"

    def __init__(self, buses: list[int] | None = None) -> None:
        self._forced_buses = buses

    @staticmethod
    def available() -> bool:
        return proc.which("ddcutil") is not None

    def probe(self) -> list[Monitor]:
        if not self.available():
            return []
        if self._forced_buses:
            displays = [(b, f"i2c-{b}", "") for b in self._forced_buses]
        else:
            displays = parse_detect(proc.run(["ddcutil", "detect", "--brief"], timeout=15))
        monitors = []
        for bus, model, connector in displays:
            monitor = Monitor(f"ddcutil:{bus}", model, connector, self)
            for control in VCP_CODES:
                try:
                    found = self.read(monitor, control)
                    if found:
                        monitor.controls[control] = found
                except DisplayError as exc:
                    _log.debug("%s: %s", model, exc)
            if monitor.controls:
                monitors.append(monitor)
        return monitors

    def _bus(self, monitor: Monitor) -> str:
        return monitor.id.split(":", 1)[1]

    def write(self, monitor: Monitor, control: str, raw: int) -> None:
        proc.run(["ddcutil", "--bus", self._bus(monitor), *_FAST, "setvcp",
                  f"{VCP_CODES[control]:x}", str(raw)], timeout=5, check=True)

    def read(self, monitor: Monitor, control: str) -> Control | None:
        out = proc.run(["ddcutil", "--bus", self._bus(monitor), "--brief", "getvcp",
                        f"{VCP_CODES[control]:x}"], timeout=5)
        current, maximum = parse_getvcp(out)
        return Control(control, current, maximum or 100)
