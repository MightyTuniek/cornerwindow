"""What a player is doing, independent of where it comes from (MPRIS or MPD)."""

from __future__ import annotations

import time
from dataclasses import dataclass

LOOP_CYCLE = ("None", "Playlist", "Track")


@dataclass
class NowPlaying:
    player: str  # unique key (MPRIS bus name, or "mpd")
    identity: str  # short id, e.g. "firefox"
    status: str  # Playing | Paused | Stopped
    title: str
    artist: str
    art_url: str
    length: float  # seconds (0 = unknown)
    position: float  # seconds at `position_time`
    position_time: float
    rate: float = 1.0
    app: str = ""  # "Firefox", "Spotify"
    app_icon: str = ""  # a Gio.Icon string ("" = unknown)
    site: str = ""  # "YouTube" when a browser tab plays it
    album: str = ""
    can_next: bool = True
    can_prev: bool = True
    can_seek: bool = False
    can_raise: bool = False
    shuffle: bool | None = None  # None = the player doesn't support it
    loop: str | None = None  # None | "None" | "Playlist" | "Track"

    @property
    def playing(self) -> bool:
        return self.status == "Playing"

    def position_now(self) -> float:
        if not self.playing:
            return self.position
        now = self.position + (time.monotonic() - self.position_time) * self.rate
        return min(now, self.length) if self.length > 0 else now

    @property
    def source(self) -> str:
        """'YouTube · Firefox' for a browser tab, 'Spotify' for an app."""
        app = self.app or self.identity.title()
        if self.site and self.site.lower() != app.lower():
            return f"{self.site} · {app}"
        return app


def next_loop(loop: str | None) -> str:
    try:
        return LOOP_CYCLE[(LOOP_CYCLE.index(loop or "None") + 1) % len(LOOP_CYCLE)]
    except ValueError:
        return "None"


def fmt_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_seek(text: str, position: float) -> float | None:
    """'+30' / '-10' (relative seconds), '1:20' or '80' (absolute) -> target seconds."""
    text = text.strip()
    if not text:
        return None
    sign = 0
    if text[0] in "+-":
        sign = 1 if text[0] == "+" else -1
        text = text[1:].strip()
    try:
        parts = [float(p) for p in text.split(":")]
    except ValueError:
        return None
    if not parts or len(parts) > 3:
        return None
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    if sign:
        return max(0.0, position + sign * seconds)
    return seconds
