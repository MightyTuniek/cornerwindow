"""Desktop appearance & notifications. GNOME (and GNOME-based: Zorin, Ubuntu, Pop) via GSettings.

Other desktops report `available() == False` and the matching toggles hide
themselves; add a backend here to support them.
"""

from __future__ import annotations

from gi.repository import Gio

from ...core import log
from ..display.nightlight import _settings

_log = log.get("desktop")
IFACE = "org.gnome.desktop.interface"
NOTIF = "org.gnome.desktop.notifications"


class DarkMode:
    def __init__(self) -> None:
        self._s = _settings(IFACE)

    def available(self) -> bool:
        return self._s is not None and "color-scheme" in self._s.props.settings_schema.list_keys()

    def is_on(self) -> bool:
        return bool(self._s) and self._s.get_string("color-scheme") == "prefer-dark"

    def watch(self, fn) -> None:
        """Call fn() whenever the desktop switches between light and dark."""
        if self._s is not None:
            self._s.connect("changed::color-scheme", lambda *_a: fn())

    def set(self, on: bool) -> None:
        if not self._s:
            return
        self._s.set_string("color-scheme", "prefer-dark" if on else "default")
        # Many themes (Zorin, Yaru, Adwaita legacy) ship as Name-Dark / Name-Light pairs;
        # legacy GTK3 apps only follow the theme name, not color-scheme.
        theme = self._s.get_string("gtk-theme")
        candidate = _counterpart_theme(theme, on)
        if candidate and _theme_exists(candidate):
            self._s.set_string("gtk-theme", candidate)


def _counterpart_theme(theme: str, dark: bool) -> str | None:
    lower = theme.lower()
    if dark:
        if lower.endswith("-dark"):
            return None
        if lower.endswith("-light"):
            return theme[:-6] + ("-Dark" if theme[-5].isupper() else "-dark")
        return theme + "-dark"
    if lower.endswith("-dark"):
        base = theme[:-5]
        light = base + ("-Light" if theme[-4].isupper() else "-light")
        return light if _theme_exists(light) else base
    return None


def _theme_exists(name: str) -> bool:
    from pathlib import Path

    roots = [Path("/usr/share/themes"), Path.home() / ".themes", Path.home() / ".local/share/themes"]
    return any((root / name).is_dir() for root in roots)


class DoNotDisturb:
    def __init__(self) -> None:
        self._s = _settings(NOTIF)

    def available(self) -> bool:
        return self._s is not None

    def is_on(self) -> bool:
        return bool(self._s) and not self._s.get_boolean("show-banners")

    def set(self, on: bool) -> None:
        if self._s:
            self._s.set_boolean("show-banners", not on)


def open_settings(panel: str) -> None:
    """Open the desktop's settings at a panel ("wifi", "bluetooth", "power")."""
    from .. import proc

    for args in (["gnome-control-center", panel], ["cinnamon-settings", panel],
                 ["systemsettings", {"wifi": "kcm_networkmanagement", "bluetooth": "kcm_bluetooth",
                                     "power": "kcm_powerdevilprofilesconfig"}.get(panel, panel)]):
        if proc.which(args[0]):
            proc.spawn(args)
            return
    _log.info("No settings app found for %s", panel)


def open_uri(uri: str) -> None:
    try:
        Gio.AppInfo.launch_default_for_uri(uri, None)
    except Exception as exc:
        _log.warning("Could not open %s: %s", uri, exc)


def app_icon(name: str) -> str:
    """A Gio.Icon string for an app id, binary or DesktopEntry ("spotify", "zen",
    "org.gnome.Rhythmbox3"), including Flatpak ids such as app.zen_browser.zen. '' if unknown."""
    if not name:
        return ""
    lower = name.lower()
    candidates = [f"{name}.desktop", f"{lower}.desktop"]
    try:
        for group in Gio.DesktopAppInfo.search(name):
            candidates += [i for i in group if i.lower().endswith(f".{lower}.desktop") or i.lower() == f"{lower}.desktop"]
    except Exception:
        pass
    for desktop_id in candidates:
        try:
            info = Gio.DesktopAppInfo.new(desktop_id)
        except TypeError:
            info = None
        if info is not None and info.get_icon() is not None:
            return info.get_icon().to_string()
    return ""
