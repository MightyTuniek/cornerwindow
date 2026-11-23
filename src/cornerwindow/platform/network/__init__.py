"""Wi-Fi, VPN and airplane mode (NetworkManager via nmcli) and Bluetooth (BlueZ via D-Bus)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gi.repository import GLib

from ...core import log
from .. import dbus, proc

_log = log.get("network")


def split_terse(line: str) -> list[str]:
    """Split one line of `nmcli -t` output: fields are ':'-separated, '\\:' is a literal colon."""
    fields, current, escaped = [], [], False
    for ch in line:
        if escaped:
            current.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(ch)
    fields.append("".join(current))
    return fields


@dataclass
class WifiNetwork:
    ssid: str
    signal: int
    secure: bool
    active: bool
    known: bool


def parse_wifi_list(text: str, known: set[str]) -> list[WifiNetwork]:
    """`nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY dev wifi list` -> one entry per SSID,
    connected first, then known, then by signal."""
    best: dict[str, WifiNetwork] = {}
    for line in text.splitlines():
        fields = split_terse(line)
        if len(fields) < 4 or not fields[1]:
            continue  # hidden networks have no SSID
        in_use, ssid, signal, security = fields[0], fields[1], fields[2], fields[3]
        try:
            strength = int(signal)
        except ValueError:
            strength = 0
        net = WifiNetwork(ssid, strength, security.strip() not in ("", "--"), in_use.strip() == "*",
                          ssid in known)
        old = best.get(ssid)
        if old is None or net.active or (not old.active and net.signal > old.signal):
            best[ssid] = net
    return sorted(best.values(), key=lambda n: (not n.active, not n.known, -n.signal))


def parse_connections(text: str) -> list[tuple[str, str, bool]]:
    """`nmcli -t -f NAME,TYPE,ACTIVE connection show` -> (name, type, active)."""
    out = []
    for line in text.splitlines():
        fields = split_terse(line)
        if len(fields) >= 3:
            out.append((fields[0], fields[1], fields[2] == "yes"))
    return out


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


    def known(self) -> set[str]:
        out = proc.run(["nmcli", "-t", "-f", "NAME,TYPE,ACTIVE", "connection", "show"], 3)
        return {name for name, kind, _a in parse_connections(out) if "wireless" in kind}

    def networks(self, rescan: bool = True) -> list[WifiNetwork]:
        out = proc.run(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "dev", "wifi", "list",
                        "--rescan", "auto" if rescan else "no"], 12)
        return parse_wifi_list(out, self.known())

    def rescan(self) -> None:
        """Ask NetworkManager for a fresh scan (returns at once; results land in a few seconds)."""
        proc.run(["nmcli", "dev", "wifi", "rescan"], 3)

    def connect(self, network: WifiNetwork) -> str:
        """Join a known or open network. Secured new ones need the desktop's dialog."""
        if network.active:
            proc.run(["nmcli", "connection", "down", "id", network.ssid], 10)
            return f"Disconnected from {network.ssid}"
        if network.known:
            ok = proc.run(["nmcli", "connection", "up", "id", network.ssid], 30)
        elif not network.secure:
            ok = proc.run(["nmcli", "dev", "wifi", "connect", network.ssid], 30)
        else:
            from ..desktop import open_settings

            open_settings("wifi")
            return f"Enter the password for {network.ssid} in Settings"
        return f"Connected to {network.ssid}" if ok else f"Could not connect to {network.ssid}"


class Vpn:
    """NetworkManager VPN and WireGuard connections."""

    TYPES = ("vpn", "wireguard")

    @staticmethod
    def available() -> bool:
        return bool(proc.which("nmcli")) and bool(Vpn().connections())

    def connections(self) -> list[tuple[str, bool]]:
        out = proc.run(["nmcli", "-t", "-f", "NAME,TYPE,ACTIVE", "connection", "show"], 3)
        return [(name, active) for name, kind, active in parse_connections(out) if kind in self.TYPES]

    def is_on(self) -> bool:
        return any(active for _n, active in self.connections())

    def active(self) -> str:
        return next((name for name, active in self.connections() if active), "")

    def up(self, name: str) -> bool:
        return bool(proc.run(["nmcli", "connection", "up", "id", name], 30))

    def down(self, name: str) -> None:
        proc.run(["nmcli", "connection", "down", "id", name], 10)


class Airplane:
    """Every radio off at once (Wi-Fi, mobile broadband via nmcli; Bluetooth via BlueZ)."""

    @staticmethod
    def available() -> bool:
        return bool(proc.which("nmcli")) or Bluetooth.available()

    def radios_on(self) -> bool:
        out = proc.run(["nmcli", "-t", "-f", "WIFI", "radio"], 2).strip() if proc.which("nmcli") else ""
        return out == "enabled" or Bluetooth().is_on()

    def set(self, on: bool, restore_bluetooth: bool = True) -> None:
        if proc.which("nmcli"):
            proc.run(["nmcli", "radio", "all", "off" if on else "on"], 5)
        if Bluetooth.available() and (on or restore_bluetooth):
            Bluetooth().set(not on)


@dataclass
class BtDevice:
    path: str
    name: str
    icon: str
    connected: bool
    battery: int | None


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

    def devices(self) -> list[BtDevice]:
        """Paired devices, connected first."""
        try:
            objects = dbus.call(dbus.system(), self.BLUEZ, "/", "org.freedesktop.DBus.ObjectManager",
                                "GetManagedObjects", None, "(a{oa{sa{sv}}})", 3000)[0]
        except GLib.Error:
            return []
        out = []
        for path, ifaces in objects.items():
            dev = ifaces.get("org.bluez.Device1")
            if not dev or not (dev.get("Paired") or dev.get("Bonded") or dev.get("Trusted")):
                continue
            battery = ifaces.get("org.bluez.Battery1", {}).get("Percentage")
            out.append(BtDevice(path, str(dev.get("Alias") or dev.get("Name") or dev.get("Address", "?")),
                                str(dev.get("Icon") or ""), bool(dev.get("Connected")),
                                int(battery) if battery is not None else None))
        return sorted(out, key=lambda d: (not d.connected, d.name.lower()))

    def connected_name(self) -> str:
        return next((d.name for d in self.devices() if d.connected), "")

    def toggle_device(self, device: BtDevice) -> str:
        method = "Disconnect" if device.connected else "Connect"
        try:
            if not device.connected and not self.is_on():
                self.set(True)
            dbus.call(dbus.system(), self.BLUEZ, device.path, "org.bluez.Device1", method, None, None, 25000)
        except GLib.Error as exc:
            _log.info("Bluetooth %s %s failed: %s", method, device.name, exc)
            return f"Could not {method.lower()} {device.name}"
        return f"{device.name} {'disconnected' if device.connected else 'connected'}"
