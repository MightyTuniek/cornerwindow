"""Wi-Fi (NetworkManager via nmcli) and Bluetooth (BlueZ via D-Bus)."""

from __future__ import annotations

from pathlib import Path

from gi.repository import GLib

from ...core import log
from .. import dbus, proc

_log = log.get("network")


class WiFi:
    @staticmethod
    def available() -> bool:
        if not proc.which("nmcli"):
            return False
        return "wifi" in proc.run(["nmcli", "-t", "-f", "TYPE", "device"], 2).split()

    def is_on(self) -> bool:
        return proc.run(["nmcli", "-t", "radio", "wifi"], 2).strip() == "enabled"

    def set(self, on: bool) -> None:
        proc.run(["nmcli", "radio", "wifi", "on" if on else "off"], 5)

    def connection(self) -> str:
        """Name of the active Wi-Fi connection, '' if none."""
        for line in proc.run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show", "--active"], 2).splitlines():
            name, _, kind = line.rpartition(":")
            if "wireless" in kind:
                return name.replace("\\:", ":")
        return ""


class Bluetooth:
    BLUEZ = "org.bluez"

    @staticmethod
    def available() -> bool:
        return any(Path("/sys/class/bluetooth").glob("hci*"))

    def _adapter(self) -> str | None:
        try:
            objects = dbus.call(dbus.system(), self.BLUEZ, "/", "org.freedesktop.DBus.ObjectManager",
                                "GetManagedObjects", None, "(a{oa{sa{sv}}})", 2000)[0]
        except GLib.Error:
            return None
        for path, ifaces in objects.items():
            if "org.bluez.Adapter1" in ifaces:
                return path
        return None

    def is_on(self) -> bool:
        path = self._adapter()
        if not path:
            return False
        try:
            return bool(dbus.get_property(dbus.system(), self.BLUEZ, path, "org.bluez.Adapter1", "Powered"))
        except GLib.Error:
            return False

    def set(self, on: bool) -> None:
        path = self._adapter()
        if path:
            dbus.set_property(dbus.system(), self.BLUEZ, path, "org.bluez.Adapter1", "Powered",
                              GLib.Variant("b", on))
