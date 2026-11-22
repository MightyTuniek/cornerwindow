"""Thin helpers over Gio's D-Bus API (no python-dbus dependency)."""

from __future__ import annotations

from typing import Any

from gi.repository import Gio, GLib

from ..core import log

_log = log.get("dbus")


def session() -> Gio.DBusConnection:
    return Gio.bus_get_sync(Gio.BusType.SESSION, None)


def system() -> Gio.DBusConnection:
    return Gio.bus_get_sync(Gio.BusType.SYSTEM, None)


def call(bus: Gio.DBusConnection, name: str, path: str, iface: str, method: str,
         args: GLib.Variant | None = None, reply: str | None = None,
         timeout_ms: int = 3000) -> Any:
    """Synchronous call (use from worker threads). Returns unpacked result."""
    result = bus.call_sync(
        name, path, iface, method, args,
        GLib.VariantType.new(reply) if reply else None,
        Gio.DBusCallFlags.NONE, timeout_ms, None,
    )
    return result.unpack() if result is not None else None


def get_property(bus: Gio.DBusConnection, name: str, path: str, iface: str, prop: str) -> Any:
    value = call(bus, name, path, "org.freedesktop.DBus.Properties", "Get",
                 GLib.Variant("(ss)", (iface, prop)), "(v)")
    return value[0] if value else None


def set_property(bus: Gio.DBusConnection, name: str, path: str, iface: str, prop: str,
                 value: GLib.Variant) -> None:
    call(bus, name, path, "org.freedesktop.DBus.Properties", "Set",
         GLib.Variant("(ssv)", (iface, prop, value)))


def list_names(bus: Gio.DBusConnection) -> list[str]:
    try:
        return call(bus, "org.freedesktop.DBus", "/org/freedesktop/DBus",
                    "org.freedesktop.DBus", "ListNames", None, "(as)")[0]
    except GLib.Error as exc:
        _log.debug("ListNames failed: %s", exc)
        return []


def name_has_owner(bus: Gio.DBusConnection, name: str) -> bool:
    try:
        return call(bus, "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                    "NameHasOwner", GLib.Variant("(s)", (name,)), "(b)")[0]
    except GLib.Error:
        return False
