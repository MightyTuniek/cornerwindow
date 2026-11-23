"""MPD (Music Player Daemon) over its own text protocol, for setups without mpDris2.

One daemon thread holds an `idle` connection and wakes only when MPD reports a change;
commands use a short-lived second connection from the worker pool. No polling.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from typing import TYPE_CHECKING

from ...core import log, tasks
from .model import NowPlaying

if TYPE_CHECKING:
    from .service import MediaService

_log = log.get("mpd")
KEY = "mpd"


def parse_response(text: str) -> list[dict[str, str]]:
    """MPD 'key: value' lines into one dict per command (split at list_OK)."""
    blocks: list[dict[str, str]] = [{}]
    for line in text.splitlines():
        if line == "list_OK":
            blocks.append({})
            continue
        if line == "OK" or line.startswith("ACK") or line.startswith("OK MPD"):
            continue
        key, sep, value = line.partition(": ")
        if sep and key not in blocks[-1]:
            blocks[-1][key] = value
    if not blocks[-1] and len(blocks) > 1:
        blocks.pop()
    return blocks


def snapshot_from(status: dict[str, str], song: dict[str, str], at: float) -> NowPlaying:
    state = {"play": "Playing", "pause": "Paused"}.get(status.get("state", ""), "Stopped")
    repeat, single = status.get("repeat") == "1", status.get("single") == "1"
    loop = "Track" if repeat and single else "Playlist" if repeat else "None"
    title = song.get("Title") or song.get("Name") or os.path.basename(song.get("file", ""))
    length = _float(status.get("duration") or song.get("duration") or song.get("Time"))
    return NowPlaying(
        player=KEY, identity="mpd", status=state, title=title,
        artist=song.get("Artist", "") or song.get("AlbumArtist", ""), album=song.get("Album", ""),
        art_url="", length=length, position=_float(status.get("elapsed")), position_time=at,
        app="MPD", app_icon="", site="", can_seek=length > 0, can_raise=False,
        shuffle=status.get("random") == "1", loop=loop,
    )


def _float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def target() -> tuple[str, int, str]:
    """(host or socket path, port, password) from MPD_HOST / MPD_PORT, like mpc does."""
    host = os.environ.get("MPD_HOST", "localhost")
    password = ""
    if "@" in host and not host.startswith("@"):
        password, host = host.split("@", 1)
    try:
        port = int(os.environ.get("MPD_PORT", "6600"))
    except ValueError:
        port = 6600
    return host, port, password


class _Conn:
    def __init__(self, host: str, port: int, password: str, timeout: float | None) -> None:
        if host.startswith("/") or host.startswith("~"):
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.settimeout(timeout)
            self.sock.connect(os.path.expanduser(host))
        else:
            self.sock = socket.create_connection((host, port), timeout=timeout)
        self.file = self.sock.makefile("r", encoding="utf-8", errors="replace", newline="\n")
        hello = self.file.readline()
        if not hello.startswith("OK MPD"):
            raise ConnectionError(f"not MPD: {hello!r}")
        if password:
            self.command(f'password "{password}"')

    def command(self, text: str) -> list[dict[str, str]]:
        self.sock.sendall((text + "\n").encode())
        lines = []
        while True:
            line = self.file.readline()
            if not line:
                raise ConnectionError("MPD closed the connection")
            line = line.rstrip("\n")
            if line == "OK":
                break
            if line.startswith("ACK"):
                raise RuntimeError(line)
            lines.append(line)
        return parse_response("\n".join(lines))

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def probe(host: str, port: int, password: str = "") -> bool:
    try:
        _Conn(host, port, password, 0.4).close()
        return True
    except (OSError, ConnectionError, RuntimeError):
        return False


class MpdPlayer:
    """One more player for the MediaService, fed by MPD's idle events."""

    def __init__(self, service: "MediaService", host: str, port: int, password: str = "") -> None:
        self.key = KEY
        self.service = service
        self.host, self.port, self.password = host, port, password
        self.identity, self.app, self.app_icon, self.desktop_entry = "mpd", "MPD", "", ""
        self.last_active = 0.0
        self._snap: NowPlaying | None = None
        self._stop = False
        self._thread = threading.Thread(target=self._loop, name="cw-mpd", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop = True

    def names(self) -> str:
        return "mpd"

    @property
    def playing(self) -> bool:
        return bool(self._snap and self._snap.playing)

    def snapshot(self) -> NowPlaying:
        if self._snap is None:
            return snapshot_from({}, {}, time.monotonic())
        return self._snap

    # -- the idle loop (worker thread) ------------------------------------------------
    def _loop(self) -> None:
        delay = 2.0
        while not self._stop:
            conn = None
            try:
                conn = _Conn(self.host, self.port, self.password, None)
                delay = 2.0
                tasks.on_main(self.service.add, self)
                while not self._stop:
                    status, song = self._read(conn)
                    tasks.on_main(self._update, snapshot_from(status, song, time.monotonic()))
                    conn.command("idle player options mixer")
            except (OSError, ConnectionError, RuntimeError) as exc:
                _log.debug("MPD connection: %s", exc)
            finally:
                if conn is not None:
                    conn.close()
            if self._stop:
                break
            tasks.on_main(self.service.remove, self.key)
            time.sleep(delay)
            delay = min(delay * 2, 60.0)

    @staticmethod
    def _read(conn: _Conn) -> tuple[dict[str, str], dict[str, str]]:
        blocks = conn.command("command_list_ok_begin\nstatus\ncurrentsong\ncommand_list_end")
        blocks += [{}] * (2 - len(blocks))
        return blocks[0], blocks[1]

    def _update(self, snap: NowPlaying) -> None:
        was = self._snap.playing if self._snap else False
        self._snap = snap
        if snap.playing:
            self.last_active = time.monotonic()
        self.service.changed(started=self if snap.playing and not was else None)

    # -- commands (worker pool, short-lived connection) --------------------------------
    def _send(self, text: str) -> None:
        def run() -> None:
            conn = _Conn(self.host, self.port, self.password, 2.0)
            try:
                conn.command(text)
            finally:
                conn.close()

        tasks.run_async(run)

    def play_pause(self) -> None:
        state = self._snap.status if self._snap else "Stopped"
        self._send("play" if state == "Stopped" else "pause 1" if state == "Playing" else "pause 0")

    def next(self) -> None:
        self._send("next")

    def previous(self) -> None:
        self._send("previous")

    def seek_to(self, seconds: float) -> None:
        self._send(f"seekcur {max(0.0, seconds):.1f}")

    def set_shuffle(self, on: bool) -> None:
        self._send(f"random {int(on)}")

    def set_loop(self, loop: str) -> None:
        repeat, single = {"Track": (1, 1), "Playlist": (1, 0)}.get(loop, (0, 0))
        self._send(f"command_list_begin\nrepeat {repeat}\nsingle {single}\ncommand_list_end")

    def fetch_position(self) -> None:
        pass  # idle events + extrapolation keep it right

    def raise_(self) -> None:
        pass
