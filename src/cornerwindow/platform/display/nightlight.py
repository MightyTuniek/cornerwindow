"""Night light (warm screen). GNOME implementation via GSettings.

GNOME only applies night light inside its schedule. To make the toggle work
"right now", turning it on switches to an all-day manual schedule and saves the
user's previous schedule; turning it off restores it.
"""

from __future__ import annotations

from gi.repository import Gio

from ...core import log
from ...core.state import State

_log = log.get("nightlight")
SCHEMA = "org.gnome.settings-daemon.plugins.color"
MIN_K, MAX_K = 1700, 4700


def _settings(schema: str) -> Gio.Settings | None:
    source = Gio.SettingsSchemaSource.get_default()
    if source is None or source.lookup(schema, True) is None:
        return None
    return Gio.Settings.new(schema)


class NightLight:
    def __init__(self, state: State) -> None:
        self._state = state
        self._s = _settings(SCHEMA)

    @property
    def available(self) -> bool:
        return self._s is not None

    def is_on(self) -> bool:
        return bool(self._s and self._s.get_boolean("night-light-enabled"))

    def set(self, on: bool) -> None:
        if not self._s:
            return
        s = self._s
        if on:
            if self._state.get("nightlight.saved") is None:
                self._state.set("nightlight.saved", {
                    "automatic": s.get_boolean("night-light-schedule-automatic"),
                    "from": s.get_double("night-light-schedule-from"),
                    "to": s.get_double("night-light-schedule-to"),
                })
            s.set_boolean("night-light-schedule-automatic", False)
            s.set_double("night-light-schedule-from", 0.0)
            s.set_double("night-light-schedule-to", 23.99)
            s.set_boolean("night-light-enabled", True)
        else:
            s.set_boolean("night-light-enabled", False)
            saved = self._state.get("nightlight.saved")
            if saved:
                s.set_boolean("night-light-schedule-automatic", saved["automatic"])
                s.set_double("night-light-schedule-from", saved["from"])
                s.set_double("night-light-schedule-to", saved["to"])
                self._state.delete("nightlight.saved")

    def warmth(self) -> float:
        """0 = barely warm, 1 = warmest."""
        if not self._s:
            return 0.5
        kelvin = self._s.get_uint("night-light-temperature")
        return 1.0 - (kelvin - MIN_K) / (MAX_K - MIN_K)

    def set_warmth(self, warmth: float) -> None:
        if self._s:
            warmth = max(0.0, min(1.0, warmth))
            self._s.set_uint("night-light-temperature", round(MAX_K - warmth * (MAX_K - MIN_K)))

    def connect(self, callback) -> None:
        if self._s:
            self._s.connect("changed::night-light-enabled", lambda *_: callback())
