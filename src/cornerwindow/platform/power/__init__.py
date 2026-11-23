"""Session power actions (logind) and idle inhibition ("caffeine")."""

from __future__ import annotations

from gi.repository import GLib

from ...core import log
from .. import dbus, proc

_log = log.get("power")
LOGIN1 = ("org.freedesktop.login1", "/org/freedesktop/login1", "org.freedesktop.login1.Manager")


def lock() -> None:
    for name, path, iface, method in (
        ("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver", "Lock"),
        ("org.freedesktop.ScreenSaver", "/ScreenSaver", "org.freedesktop.ScreenSaver", "Lock"),
    ):
        try:
            dbus.call(dbus.session(), name, path, iface, method)
            return
        except GLib.Error:
            continue
    proc.run(["loginctl", "lock-session"], 3)


def _login1(method: str) -> None:
    dbus.call(dbus.system(), *LOGIN1, method, GLib.Variant("(b)", (True,)), timeout_ms=10000)


def suspend() -> None:
    _login1("Suspend")


def reboot() -> None:
    _login1("Reboot")


def poweroff() -> None:
    _login1("PowerOff")


def logout() -> None:
    try:
        dbus.call(dbus.session(), "org.gnome.SessionManager", "/org/gnome/SessionManager",
                  "org.gnome.SessionManager", "Logout", GLib.Variant("(u)", (0,)))
    except GLib.Error:
        proc.run(["loginctl", "terminate-session", ""], 3)


class Caffeine:
    """Keep the screen awake. Uses GNOME SessionManager or freedesktop ScreenSaver Inhibit."""

    def __init__(self) -> None:
        self._cookie: tuple[str, int] | None = None

    def available(self) -> bool:
        bus = dbus.session()
        return (dbus.name_has_owner(bus, "org.gnome.SessionManager")
                or dbus.name_has_owner(bus, "org.freedesktop.ScreenSaver"))

    def is_on(self) -> bool:
        return self._cookie is not None

    def set(self, on: bool) -> None:
        bus = dbus.session()
        if on and self._cookie is None:
            try:
                cookie = dbus.call(bus, "org.gnome.SessionManager", "/org/gnome/SessionManager",
                                   "org.gnome.SessionManager", "Inhibit",
                                   GLib.Variant("(susu)", ("cornerwindow", 0, "Caffeine", 4 | 8)), "(u)")[0]
                self._cookie = ("gnome", cookie)
            except GLib.Error:
                cookie = dbus.call(bus, "org.freedesktop.ScreenSaver", "/ScreenSaver",
                                   "org.freedesktop.ScreenSaver", "Inhibit",
                                   GLib.Variant("(ss)", ("cornerwindow", "Caffeine")), "(u)")[0]
                self._cookie = ("fdo", cookie)
        elif not on and self._cookie is not None:
            kind, cookie = self._cookie
            self._cookie = None
            try:
                if kind == "gnome":
                    dbus.call(bus, "org.gnome.SessionManager", "/org/gnome/SessionManager",
                              "org.gnome.SessionManager", "Uninhibit", GLib.Variant("(u)", (cookie,)))
                else:
                    dbus.call(bus, "org.freedesktop.ScreenSaver", "/ScreenSaver",
                              "org.freedesktop.ScreenSaver", "UnInhibit", GLib.Variant("(u)", (cookie,)))
            except GLib.Error as exc:
                _log.warning("Uninhibit failed: %s", exc)


PROFILES_BUS = (("org.freedesktop.UPower.PowerProfiles", "/org/freedesktop/UPower/PowerProfiles",
                 "org.freedesktop.UPower.PowerProfiles"),
                ("net.hadess.PowerProfiles", "/net/hadess/PowerProfiles", "net.hadess.PowerProfiles"))
PROFILE_NAMES = {"power-saver": "Saver", "balanced": "Balanced", "performance": "Performance"}


class PowerProfiles:
    """power-profiles-daemon (or tuned-ppd): balanced / performance / power-saver."""

    def __init__(self) -> None:
        self._where: tuple[str, str, str] | None = None

    def _find(self) -> tuple[str, str, str] | None:
        if self._where is None:
            for where in PROFILES_BUS:
                try:
                    dbus.get_property(dbus.system(), *where, "ActiveProfile")
                except GLib.Error:
                    continue
                self._where = where
                break
        return self._where

    def available(self) -> bool:
        return self._find() is not None

    def profiles(self) -> list[str]:
        where = self._find()
        if where is None:
            return []
        try:
            items = dbus.get_property(dbus.system(), *where, "Profiles") or []
        except GLib.Error:
            return []
        found = [str(i.get("Profile")) for i in items if i.get("Profile")]
        order = list(PROFILE_NAMES)
        return sorted(found, key=lambda p: order.index(p) if p in order else 99)

    def active(self) -> str:
        where = self._find()
        if where is None:
            return ""
        try:
            return str(dbus.get_property(dbus.system(), *where, "ActiveProfile") or "")
        except GLib.Error:
            return ""

    def set(self, profile: str) -> None:
        where = self._find()
        if where is not None:
            dbus.set_property(dbus.system(), *where, "ActiveProfile", GLib.Variant("s", profile))

    def cycle(self) -> str:
        """balanced -> performance -> power-saver -> balanced (only the ones offered)."""
        offered = self.profiles()
        ring = [p for p in ("balanced", "performance", "power-saver") if p in offered] or offered
        if not ring:
            return ""
        current = self.active()
        following = ring[(ring.index(current) + 1) % len(ring)] if current in ring else ring[0]
        self.set(following)
        return following
