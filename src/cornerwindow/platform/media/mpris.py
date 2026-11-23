"""MPRIS2 over Gio D-Bus, event driven (PropertiesChanged), no polling except Position.

Every player that speaks MPRIS shows up: Spotify, VLC, mpv, Rhythmbox, Cider (Apple
Music), Strawberry, KDE Connect (your phone), and Firefox / Chrome tabs (YouTube,
SoundCloud, Apple Music web, YouTube Music...).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from gi.repository import Gio, GLib

from ...core import log
from .model import NowPlaying
from .sites import clean_app_name, site_for, strip_site_suffix

if TYPE_CHECKING:
    from .service import MediaService

_log = log.get("mpris")
PREFIX = "org.mpris.MediaPlayer2."
PATH = "/org/mpris/MediaPlayer2"
ROOT_IFACE = "org.mpris.MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
NO_TRACK = "/org/mpris/MediaPlayer2/TrackList/NoTrack"


def _desktop_icon(entry: str) -> str:
    from ..desktop import app_icon

    return app_icon(entry)


class MprisPlayer:
    def __init__(self, name: str, service: "MediaService") -> None:
        self.key = name
        self.service = service
        self.last_active = time.monotonic()
        self.position = 0.0
        self.position_time = time.monotonic()
        self._status = None
        self.proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None,
            name, PATH, PLAYER_IFACE, None)
        self.proxy.connect("g-properties-changed", self._changed)
        self.root = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None,
            name, PATH, ROOT_IFACE, None)
        self.identity = name[len(PREFIX):].split(".")[0]
        self.app = clean_app_name(self._root_prop("Identity", ""), self.identity)
        self.desktop_entry = str(self._root_prop("DesktopEntry", "") or "")
        self.app_icon = _desktop_icon(self.desktop_entry) or _desktop_icon(self.identity)
        self._status = self.prop("PlaybackStatus")

    # -- properties -----------------------------------------------------------------
    def prop(self, key: str, default=None):
        value = self.proxy.get_cached_property(key)
        return value.unpack() if value is not None else default

    def _root_prop(self, key: str, default=None):
        value = self.root.get_cached_property(key)
        return value.unpack() if value is not None else default

    def names(self) -> str:
        """Everything a [media] ignore/prefer pattern may match, lower case."""
        return f"{self.key} {self.identity} {self.app} {self.desktop_entry}".lower()

    @property
    def playing(self) -> bool:
        return self.prop("PlaybackStatus") == "Playing"

    def _changed(self, *_args) -> None:
        status = self.prop("PlaybackStatus")
        if status == "Playing":
            self.last_active = time.monotonic()
        started = status == "Playing" and self._status != "Playing"
        self._status = status
        self.fetch_position()
        self.service.changed(started=self if started else None)

    def snapshot(self) -> NowPlaying:
        meta = self.prop("Metadata", {}) or {}
        artist = meta.get("xesam:artist", "")
        if isinstance(artist, list):
            artist = ", ".join(str(a) for a in artist)
        title = str(meta.get("xesam:title", "") or "")
        art = str(meta.get("mpris:artUrl", "") or "")
        site = site_for(str(meta.get("xesam:url", "") or ""), art, title)
        shuffle = self.prop("Shuffle")
        loop = self.prop("LoopStatus")
        return NowPlaying(
            player=self.key, identity=self.identity,
            status=self.prop("PlaybackStatus", "Stopped") or "Stopped",
            title=strip_site_suffix(title) if site else title,
            artist=str(artist or ""),
            album=str(meta.get("xesam:album", "") or ""),
            art_url=art,
            length=float(meta.get("mpris:length", 0) or 0) / 1e6,
            position=self.position, position_time=self.position_time,
            rate=float(self.prop("Rate", 1.0) or 1.0),
            app=self.app, app_icon=self.app_icon, site=site,
            can_next=bool(self.prop("CanGoNext", True)),
            can_prev=bool(self.prop("CanGoPrevious", True)),
            can_seek=bool(self.prop("CanSeek", False)) and float(meta.get("mpris:length", 0) or 0) > 0,
            can_raise=bool(self._root_prop("CanRaise", False)),
            shuffle=None if shuffle is None else bool(shuffle),
            loop=None if loop is None else str(loop),
        )

    def _track_id(self) -> str:
        meta = self.prop("Metadata", {}) or {}
        track = str(meta.get("mpris:trackid", "") or "")
        return track if track.startswith("/") and track != NO_TRACK else ""

    # -- position -------------------------------------------------------------------
    def fetch_position(self) -> None:
        def done(proxy, result) -> None:
            try:
                value = proxy.call_finish(result).unpack()[0]
                self.position = float(value) / 1e6
                self.position_time = time.monotonic()
                self.service.changed()
            except GLib.Error:
                pass

        self.proxy.call(f"{PROPS_IFACE}.Get", GLib.Variant("(ss)", (PLAYER_IFACE, "Position")),
                        Gio.DBusCallFlags.NONE, 1000, None, done)

    # -- control --------------------------------------------------------------------
    def call(self, method: str, args: GLib.Variant | None = None) -> None:
        self.proxy.call(method, args, Gio.DBusCallFlags.NONE, 2000, None, None)

    def play_pause(self) -> None:
        self.call("PlayPause")

    def next(self) -> None:
        self.call("Next")

    def previous(self) -> None:
        self.call("Previous")

    def seek_to(self, seconds: float) -> None:
        seconds = max(0.0, seconds)
        track = self._track_id()
        if track:
            self.call("SetPosition", GLib.Variant("(ox)", (track, int(seconds * 1e6))))
        else:  # players without track ids still take a relative Seek
            now = self.snapshot().position_now()
            self.call("Seek", GLib.Variant("(x)", (int((seconds - now) * 1e6),)))
        self.position, self.position_time = seconds, time.monotonic()
        self.service.changed()
        GLib.timeout_add(300, lambda: self.fetch_position() and False)

    def _set(self, prop: str, value: GLib.Variant) -> None:
        self.proxy.call(f"{PROPS_IFACE}.Set", GLib.Variant("(ssv)", (PLAYER_IFACE, prop, value)),
                        Gio.DBusCallFlags.NONE, 2000, None, None)

    def set_shuffle(self, on: bool) -> None:
        self._set("Shuffle", GLib.Variant("b", on))

    def set_loop(self, loop: str) -> None:
        self._set("LoopStatus", GLib.Variant("s", loop))

    def raise_(self) -> None:
        self.root.call("Raise", None, Gio.DBusCallFlags.NONE, 2000, None, None)


class MprisWatcher:
    """Tracks org.mpris.MediaPlayer2.* names on the session bus."""

    def __init__(self, service: "MediaService") -> None:
        self.service = service
        self._conn: Gio.DBusConnection | None = None

    def start(self) -> None:
        try:
            self._conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error as exc:
            _log.info("No session bus: %s", exc)
            return
        self._conn.signal_subscribe("org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
                                    "/org/freedesktop/DBus", None, Gio.DBusSignalFlags.NONE,
                                    self._owner_changed)
        try:
            names = self._conn.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                                         "org.freedesktop.DBus", "ListNames", None,
                                         GLib.VariantType.new("(as)"), Gio.DBusCallFlags.NONE, 2000,
                                         None).unpack()[0]
        except GLib.Error as exc:
            _log.info("ListNames failed: %s", exc)
            return
        for name in names:
            if name.startswith(PREFIX):
                self._add(name)

    def _add(self, name: str) -> None:
        try:
            self.service.add(MprisPlayer(name, self.service))
        except GLib.Error as exc:
            _log.debug("Cannot watch %s: %s", name, exc)

    def _owner_changed(self, _conn, _sender, _path, _iface, _signal, params) -> None:
        name, _old, new = params.unpack()
        if not name.startswith(PREFIX):
            return
        if new:
            self._add(name)
        else:
            self.service.remove(name)
