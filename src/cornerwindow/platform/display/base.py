"""Display model shared by all backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

BRIGHTNESS = "brightness"
CONTRAST = "contrast"

# MCCS VCP feature codes
VCP_CODES = {BRIGHTNESS: 0x10, CONTRAST: 0x12}


@dataclass
class Control:
    name: str
    value: int  # raw monitor units
    maximum: int = 100
    minimum: int = 0

    @property
    def percent(self) -> float:
        span = max(self.maximum - self.minimum, 1)
        return 100.0 * (self.value - self.minimum) / span

    def raw_from_percent(self, percent: float) -> int:
        percent = max(0.0, min(100.0, percent))
        return round(self.minimum + (self.maximum - self.minimum) * percent / 100.0)


@dataclass
class Monitor:
    id: str  # stable id, e.g. "ddc:4" or "backlight:intel_backlight"
    name: str  # human name, e.g. "Acer VG240Y"
    connector: str = ""  # DRM connector, e.g. "HDMI-A-1"
    backend: "DisplayBackend | None" = field(default=None, repr=False)
    controls: dict[str, Control] = field(default_factory=dict)

    def supports(self, control: str) -> bool:
        return control in self.controls


class DisplayBackend(ABC):
    name = "base"

    @abstractmethod
    def probe(self) -> list[Monitor]:
        """Return the monitors this backend can drive (with current values). May block."""

    @abstractmethod
    def write(self, monitor: Monitor, control: str, raw: int) -> None:
        """Set a raw value. Called from a worker thread. May block."""

    def read(self, monitor: Monitor, control: str) -> Control | None:
        """Re-read a control. Called from a worker thread."""
        return monitor.controls.get(control)


class DisplayError(RuntimeError):
    pass
