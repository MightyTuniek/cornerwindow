"""Batteries via UPower: the laptop's own, plus mice, keyboards, headsets, controllers, phones.

Event driven: DeviceAdded/DeviceRemoved and each device's PropertiesChanged. Nothing polls.
"""

from __future__ import annotations

from dataclasses import dataclass

from gi.repository import Gio, GLib

from ...core import log
from ...core.bus import EventBus

_log = log.get("upower")
NAME = "org.freedesktop.UPower"
PATH = "/org/freedesktop/UPower"
DEVICE_IFACE = "org.freedesktop.UPower.Device"

# UPower device types we show, and how to draw them
KINDS = {
    2: "battery", 3: "ups", 5: "mouse", 6: "keyboard", 7: "pda", 8: "phone", 9: "media", 10: "tablet",
    12: "gamepad", 13: "pen", 14: "touchpad", 17: "headset", 18: "speakers", 19: "headphones",
    21: "audio", 22: "remote", 26: "watch", 28: "device",
}
STATES = {1: "charging", 2: "discharging", 3: "empty", 4: "full", 5: "pending-charge", 6: "pending-discharge"}


@dataclass
class BatteryDevice:
    path: str
    kind: str  # "battery", "mouse", "headset"...
    model: str
    percent: float
    state: str  # charging | discharging | full | empty | ...
    time_to_empty: int  # seconds, 0 = unknown
    time_to_full: int
    is_laptop: bool  # powers this computer

    @property
    def charging(self) -> bool:
        return self.state in ("charging", "pending-charge")


def device_from(path: str, props: dict) -> BatteryDevice | None:
    """A BatteryDevice from UPower properties, or None for things that aren't batteries."""
    kind = KINDS.get(int(props.get("Type", 0) or 0))
    if kind is None or not props.get("IsPresent", True):
        return None
    percent = float(props.get("Percentage", 0) or 0)
    level = int(props.get("BatteryLevel", 0) or 0)
    if percent <= 0 and level > 1:  # coarse-level devices (some mice): none/low/critical/normal/high/full
        percent = {3: 5, 4: 20, 6: 55, 7: 80, 8: 100}.get(level, 0)
    return BatteryDevice(
        path=path, kind=kind,
        model=str(props.get("Model") or props.get("NativePath") or kind.title()),
        percent=percent,
        state=STATES.get(int(props.get("State", 0) or 0), "unknown"),
        time_to_empty=int(props.get("TimeToEmpty", 0) or 0),
        time_to_full=int(props.get("TimeToFull", 0) or 0),
        is_laptop=kind == "battery" and bool(props.get("PowerSupply", False)),
    )


def sort_devices(devices: list[BatteryDevice]) -> list[BatteryDevice]:
    return sorted(devices, key=lambda d: (not d.is_laptop, d.kind, d.model.lower()))


class Batteries:
    """Publishes `battery.changed` with the current device list."""

    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self.devices: dict[str, BatteryDevice] = {}
        self._proxies: dict[str, Gio.DBusProxy] = {}
        self._conn: Gio.DBusConnection | None = None
        self.running = False

    def start(self) -> None:
        try:
            self._conn = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            paths = self._conn.call_sync(NAME, PATH, NAME, "EnumerateDevices", None,
                                         GLib.VariantType.new("(ao)"), Gio.DBusCallFlags.NONE, 2000,
                                         None).unpack()[0]
        except GLib.Error as exc:
            _log.info("UPower not available: %s", exc)
            return
        self.running = True
        for signal in ("DeviceAdded", "DeviceRemoved"):
            self._conn.signal_subscribe(NAME, NAME, signal, PATH, None, Gio.DBusSignalFlags.NONE,
                                        self._added_removed)
        for path in paths:
            self._watch(path)
        self._publish()

    def _watch(self, path: str) -> None:
        try:
            proxy = Gio.DBusProxy.new_sync(self._conn, Gio.DBusProxyFlags.NONE, None, NAME, path,
                                           DEVICE_IFACE, None)
        except GLib.Error as exc:
            _log.debug("Cannot watch %s: %s", path, exc)
            return
        self._proxies[path] = proxy
        proxy.connect("g-properties-changed", lambda p, *_a: self._read(path, p) and self._publish())
        self._read(path, proxy)

    def _read(self, path: str, proxy: Gio.DBusProxy) -> bool:
        props = {}
        for key in proxy.get_cached_property_names() or []:
            value = proxy.get_cached_property(key)
            if value is not None:
                props[key] = value.unpack()
        device = device_from(path, props)
        if device is None:
            self.devices.pop(path, None)
        else:
            self.devices[path] = device
        return True

    def _added_removed(self, _conn, _sender, _path, _iface, signal, params) -> None:
        (path,) = params.unpack()
        if signal == "DeviceAdded":
            self._watch(path)
        else:
            self._proxies.pop(path, None)
            self.devices.pop(path, None)
        self._publish()

    def _publish(self) -> None:
        self.bus.publish("battery.changed", devices=self.current())

    def current(self) -> list[BatteryDevice]:
        return sort_devices(list(self.devices.values()))
