"""DisplayService: what modules talk to. Picks backends, caches values, coalesces writes."""

from __future__ import annotations

import threading
from typing import Callable

from ...core import log, tasks
from ...core.bus import EventBus
from .backlight import BacklightBackend
from .base import BRIGHTNESS, CONTRAST, DisplayBackend, DisplayError, Monitor
from .ddc_i2c import DdcI2cBackend
from .ddcutil import DdcutilBackend
from .gamma import GammaBackend

_log = log.get("display")


def build_backends(config: dict) -> list[DisplayBackend]:
    choice = config.get("backend", "auto")
    bus = config.get("i2c_bus")
    buses = [bus] if isinstance(bus, int) else (bus if isinstance(bus, list) else None)
    available = {
        "ddc": lambda: DdcI2cBackend(buses),
        "ddcutil": lambda: DdcutilBackend(buses),
        "backlight": BacklightBackend,
        "gamma": GammaBackend,
    }
    if choice != "auto":
        order = [choice] if isinstance(choice, str) else list(choice)
    else:
        order = ["ddc", "ddcutil", "backlight", "gamma"]
    return [available[name]() for name in order if name in available]


class DisplayService:
    """Brightness/contrast across all controllable monitors, in percent (0-100)."""

    def __init__(self, config: dict, bus: EventBus) -> None:
        self.config = config
        self.bus = bus
        self.monitors: list[Monitor] = []
        self.ready = threading.Event()
        self.problems: list[str] = []
        self._percent: dict[str, float] = {}
        self._writer = tasks.LatestWins(self._apply, min_interval=0.0, name="display-writer")

    # -- discovery -------------------------------------------------------------------------
    def start(self, on_ready: Callable[[], None] | None = None) -> None:
        tasks.run_async(self._probe, lambda _: self._probed(on_ready))

    def probe_blocking(self) -> None:
        self._probe()
        self._probed(None)

    def _probe(self) -> None:
        seen_connectors: set[str] = set()
        has_external = False
        for backend in build_backends(self.config):
            # gamma is a fallback only when nothing better was found
            if backend.name == "gamma" and self.monitors:
                continue
            try:
                found = backend.probe()
            except Exception as exc:
                self.problems.append(f"{backend.name}: {exc}")
                _log.debug("Backend %s failed", backend.name, exc_info=True)
                continue
            for monitor in found:
                if monitor.connector and monitor.connector in seen_connectors:
                    continue  # already driven by a faster backend
                if backend.name in ("ddc", "ddcutil") and not monitor.connector and has_external:
                    continue
                seen_connectors.add(monitor.connector)
                has_external = has_external or backend.name in ("ddc", "ddcutil")
                self.monitors.append(monitor)
                _log.info("Display: %s via %s (%s)", monitor.name, backend.name,
                          ", ".join(f"{k}={v.value}/{v.maximum}" for k, v in monitor.controls.items()))
        for control in (BRIGHTNESS, CONTRAST):
            values = [m.controls[control].percent for m in self.monitors if m.supports(control)]
            if values:
                self._percent[control] = values[0]
        self.ready.set()  # set from the worker so main-thread callers may wait on it

    def _probed(self, on_ready: Callable[[], None] | None) -> None:
        self.bus.publish("display.ready")
        if on_ready:
            on_ready()

    # -- queries ---------------------------------------------------------------------------
    def supports(self, control: str) -> bool:
        return any(m.supports(control) for m in self.monitors)

    def percent(self, control: str) -> float | None:
        return self._percent.get(control)

    def backend_names(self) -> list[str]:
        return sorted({m.backend.name for m in self.monitors if m.backend})

    # -- changes ---------------------------------------------------------------------------
    def limit(self, control: str) -> float:
        """Highest percent anything may set (config [display] limits), default 100."""
        limits = self.config.get("limits", {}) or {}
        try:
            return max(1.0, min(100.0, float(limits.get(control, 100))))
        except (TypeError, ValueError):
            return 100.0

    def set_percent(self, control: str, percent: float, source: str = "") -> float:
        """Optimistic: cache + notify immediately, hardware catches up in the background.
        Values are clamped to the configured limit, whoever asks (slider, preset, CLI)."""
        percent = max(0.0, min(self.limit(control), float(percent)))
        self._percent[control] = percent
        self._writer.submit(control, percent)
        self.bus.publish("display.changed", control=control, percent=percent, source=source)
        return percent

    def step(self, control: str, delta: float, source: str = "") -> float | None:
        current = self.percent(control)
        if current is None:
            return None
        return self.set_percent(control, round(current + delta), source)

    def _apply(self, control: str, percent: float) -> None:
        for monitor in self.monitors:
            ctl = monitor.controls.get(control)
            if not ctl or not monitor.backend:
                continue
            raw = ctl.raw_from_percent(percent)
            if raw == ctl.value:
                continue
            try:
                monitor.backend.write(monitor, control, raw)
                ctl.value = raw
            except (DisplayError, OSError, RuntimeError) as exc:
                _log.warning("Setting %s on %s failed: %s", control, monitor.name, exc)

    def refresh(self) -> None:
        """Re-read real values (the user may have used the monitor's own buttons)."""
        if self._writer.busy():
            return

        def read() -> dict[str, float]:
            fresh = {}
            for monitor in self.monitors:
                for control in list(monitor.controls):
                    try:
                        ctl = monitor.backend.read(monitor, control)
                    except Exception as exc:
                        _log.debug("Refresh of %s failed: %s", control, exc)
                        continue
                    if ctl:
                        monitor.controls[control] = ctl
                        fresh.setdefault(control, ctl.percent)
            return fresh

        def done(fresh: dict[str, float]) -> None:
            if self._writer.busy():
                return
            for control, percent in fresh.items():
                if abs(self._percent.get(control, -1) - percent) >= 1:
                    self._percent[control] = percent
                    self.bus.publish("display.changed", control=control, percent=percent, source="refresh")

        tasks.run_async(read, done)
