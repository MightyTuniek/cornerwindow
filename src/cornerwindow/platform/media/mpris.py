"""MPRIS2 over Gio D-Bus, event driven (PropertiesChanged), no polling except Position."""

from __future__ import annotations

import time
from dataclasses import dataclass

from gi.repository import Gio, GLib

from ...core import log
from ...core.bus import EventBus

_log = log.get("mpris")
PREFIX = "org.mpris.MediaPlayer2."
PATH = "/org/mpris/MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"


@dataclass
class NowPlaying:
    player: str  # bus name
    identity: str  # e.g. "firefox"
    status: str  # Playing | Paused | Stopped
    title: str
    artist: str
    art_url: str
    length: float  # seconds (0 = unknown)
    position: float  # seconds at `position_time`
    position_time: float
    rate: float = 1.0

    @property
    def playing(self) -> bool:
        return self.status == "Playing"

    def position_now(self) -> float:
        if not self.playing:
            return self.position
        return self.position + (time.monotonic() - self.position_time) * self.rate


class Player:
    def __init__(self, name: str, service: "MediaService") -> None:
        self.name = name
        self.service = service
        self.last_active = time.monotonic()
        self.position = 0.0
        self.position_time = time.monotonic()
        self.proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None,
            name, PATH, PLAYER_IFACE, None)
        self.proxy.connect("g-properties-changed", self._changed)

    def prop(self, key: str, default=None):
        value = self.proxy.get_cached_property(key)
        return value.unpack() if value is not None else default

    def _changed(self, *_args) -> None:
        if self.prop("PlaybackStatus") == "Playing":
            self.last_active = time.monotonic()
        self.fetch_position()
        self.service.changed()

    def snapshot(self) -> NowPlaying:
        meta = self.prop("Metadata", {}) or {}
        artist = meta.get("xesam:artist", "")
        if isinstance(artist, list):
            artist = ", ".join(artist)
        identity = self.name[len(PREFIX):].split(".")[0]
        return NowPlaying(
            player=self.name, identity=identity,
            status=self.prop("PlaybackStatus", "Stopped") or "Stopped",
            title=str(meta.get("xesam:title", "") or ""),
            artist=str(artist or ""),
            art_url=str(meta.get("mpris:artUrl", "") or ""),
            length=float(meta.get("mpris:length", 0) or 0) / 1e6,
            position=self.position, position_time=self.position_time,
            rate=float(self.prop("Rate", 1.0) or 1.0),
        )

    def fetch_position(self) -> None:
        def done(proxy, result) -> None:
            try:
                value = proxy.call_finish(result).unpack()[0]
                self.position = float(value) / 1e6
                self.position_time = time.monotonic()
                self.service.changed()
            except GLib.Error:
                pass

        self.proxy.call("org.freedesktop.DBus.Properties.Get",
                        GLib.Variant("(ss)", (PLAYER_IFACE, "Position")),
                        Gio.DBusCallFlags.NONE, 1000, None, done)

    def call(self, method: str) -> None:
        self.proxy.call(method, None, Gio.DBusCallFlags.NONE, 2000, None, None)


class MediaService:
    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self.players: dict[str, Player] = {}
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
        names = self._conn.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                                     "org.freedesktop.DBus", "ListNames", None,
                                     GLib.VariantType.new("(as)"), Gio.DBusCallFlags.NONE, 2000,
                                     None).unpack()[0]
        for name in names:
            if name.startswith(PREFIX):
                self._add(name)

    def _add(self, name: str) -> None:
        try:
            self.players[name] = Player(name, self)
        except GLib.Error as exc:
            _log.debug("Cannot watch %s: %s", name, exc)

    def _owner_changed(self, _conn, _sender, _path, _iface, _signal, params) -> None:
        name, old, new = params.unpack()
        if not name.startswith(PREFIX):
            return
        if new and name not in self.players:
            self._add(name)
        elif not new:
            self.players.pop(name, None)
        self.changed()

    def changed(self) -> None:
        self.bus.publish("media.changed", now=self.current())

    def current(self) -> NowPlaying | None:
        if not self.players:
            return None
        ranked = sorted(self.players.values(),
                        key=lambda p: (p.prop("PlaybackStatus") == "Playing", p.last_active), reverse=True)
        snap = ranked[0].snapshot()
        if not snap.title and snap.status == "Stopped":
            return None
        return snap

    def _active(self) -> Player | None:
        now = self.current()
        return self.players.get(now.player) if now else None

    def play_pause(self) -> None:
        player = self._active()
        if player:
            player.call("PlayPause")

    def next(self) -> None:
        player = self._active()
        if player:
            player.call("Next")

    def previous(self) -> None:
        player = self._active()
        if player:
            player.call("Previous")

    def refresh_position(self) -> None:
        player = self._active()
        if player:
            player.fetch_position()
