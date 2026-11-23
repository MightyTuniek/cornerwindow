"""MediaService: every player (MPRIS + MPD) behind one interface, and which one is shown.

The shown player is the one you picked (it sticks until it goes away or another player
starts playing), else whatever plays, else the most recently active one.
"""

from __future__ import annotations

from typing import Any, Protocol

from ...core import log
from ...core.bus import EventBus
from .model import NowPlaying, next_loop

_log = log.get("media")


class Player(Protocol):
    key: str
    last_active: float

    @property
    def playing(self) -> bool: ...
    def names(self) -> str: ...
    def snapshot(self) -> NowPlaying: ...
    def play_pause(self) -> None: ...
    def next(self) -> None: ...
    def previous(self) -> None: ...
    def seek_to(self, seconds: float) -> None: ...
    def set_shuffle(self, on: bool) -> None: ...
    def set_loop(self, loop: str) -> None: ...
    def fetch_position(self) -> None: ...
    def raise_(self) -> None: ...


def matches(names: str, patterns: list[str]) -> bool:
    return any(p and p.lower() in names for p in patterns)


def rank(players: list[Any], selected: str | None, prefer: list[str]) -> list[Any]:
    """Best first: picked, then playing, then preferred, then most recently active."""
    def key(p) -> tuple:
        pref = next((i for i, pat in enumerate(prefer) if pat and pat.lower() in p.names()), len(prefer))
        return (p.key != selected, not p.playing, pref, -p.last_active)

    return sorted(players, key=key)


class MediaService:
    def __init__(self, bus: EventBus, config: dict | None = None) -> None:
        self.bus = bus
        self.players: dict[str, Player] = {}
        self.selected: str | None = None
        self.config = config or {}
        self._mpd = None

    # -- config -------------------------------------------------------------------------
    @property
    def ignore(self) -> list[str]:
        return [str(p) for p in self.config.get("ignore", ["playerctld"]) or []]

    @property
    def prefer(self) -> list[str]:
        return [str(p) for p in self.config.get("prefer", []) or []]

    def reconfigure(self, config: dict) -> None:
        self.config = config
        for key in [k for k, p in self.players.items() if matches(p.names(), self.ignore)]:
            self.players.pop(key, None)
        self.changed()

    # -- lifecycle ----------------------------------------------------------------------
    def start(self) -> None:
        from .mpris import MprisWatcher

        MprisWatcher(self).start()
        self._start_mpd()

    def _start_mpd(self) -> None:
        setting = self.config.get("mpd", "auto")
        if setting is False or str(setting).lower() in ("false", "off", "no"):
            return
        from ...core import tasks
        from . import mpd

        host, port, password = mpd.target()
        host = str(self.config.get("mpd_host") or host)
        port = int(self.config.get("mpd_port") or port)

        def check() -> bool:
            return mpd.probe(host, port, password)

        def done(found: bool) -> None:
            if not found:
                _log.debug("No MPD at %s:%s", host, port)
                return
            if any("mpd" in p.names() for p in self.players.values()):
                _log.info("MPD found, but mpDris2 already shows it over MPRIS")
                return
            self._mpd = mpd.MpdPlayer(self, host, port, password)
            self._mpd.start()

        tasks.run_async(check, done)

    def add(self, player: Player) -> None:
        if matches(player.names(), self.ignore):
            _log.debug("Ignoring player %s", player.key)
            return
        self.players[player.key] = player
        self.changed()

    def remove(self, key: str) -> None:
        if self.players.pop(key, None) is not None:
            if self.selected == key:
                self.selected = None
            self.changed()

    # -- state --------------------------------------------------------------------------
    def changed(self, started: Player | None = None) -> None:
        if started is not None and self.selected and started.key != self.selected:
            self.selected = None  # something else started: follow it
        self.bus.publish("media.changed", now=self.current())

    def ranked(self) -> list[Player]:
        return rank(list(self.players.values()), self.selected, self.prefer)

    def current(self) -> NowPlaying | None:
        for player in self.ranked():
            snap = player.snapshot()
            if snap.title:
                return snap
        return None

    def choices(self) -> list[NowPlaying]:
        """Players worth switching to (they have something loaded), best first."""
        return [s for s in (p.snapshot() for p in self.ranked()) if s.title]

    def select(self, key: str) -> None:
        if key in self.players:
            self.selected = key
            self.changed()

    def select_by_name(self, text: str) -> str | None:
        text = text.strip().lower()
        for snap in self.choices():
            if text in f"{snap.app} {snap.site} {snap.identity}".lower():
                self.select(snap.player)
                return snap.source
        return None

    def _active(self) -> Player | None:
        now = self.current()
        return self.players.get(now.player) if now else None

    # -- control (on the shown player) --------------------------------------------------
    def _do(self, fn: str, *args) -> None:
        player = self._active()
        if player is not None:
            getattr(player, fn)(*args)

    def play_pause(self) -> None:
        self._do("play_pause")

    def next(self) -> None:
        self._do("next")

    def previous(self) -> None:
        self._do("previous")

    def seek_to(self, seconds: float) -> None:
        self._do("seek_to", seconds)

    def seek_by(self, delta: float) -> None:
        now = self.current()
        if now and now.can_seek:
            self.seek_to(max(0.0, min(now.position_now() + delta, now.length - 1)))

    def toggle_shuffle(self) -> bool | None:
        now = self.current()
        if now is None or now.shuffle is None:
            return None
        self._do("set_shuffle", not now.shuffle)
        return not now.shuffle

    def cycle_loop(self) -> str | None:
        now = self.current()
        if now is None or now.loop is None:
            return None
        loop = next_loop(now.loop)
        self._do("set_loop", loop)
        return loop

    def raise_player(self) -> None:
        self._do("raise_")

    def refresh_position(self) -> None:
        self._do("fetch_position")
