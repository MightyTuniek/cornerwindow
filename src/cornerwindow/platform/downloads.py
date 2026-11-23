"""Downloads in progress and just finished, from the downloads folder (no browser extension).

Browsers write to a partial file ("x.zip.part", "x.zip.crdownload") and rename it when done.
`Tracker` is the pure logic (fed paths and sizes, so the tests can drive it); `DownloadWatcher`
feeds it from a Gio file monitor, which costs nothing while nothing changes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from ..core import log

_log = log.get("downloads")
PARTIAL = (".part", ".crdownload", ".download", ".partial", ".opdownload", ".tmp")


@dataclass
class Download:
    path: Path  # the final name ("x.zip"), also while it's still a partial file
    size: int = 0
    started: float = 0.0
    finished: float | None = None  # None: still downloading

    @property
    def name(self) -> str:
        return self.path.name


def final_name(path: Path) -> Path | None:
    """x.zip.part -> x.zip; None when it isn't a partial file. Firefox also uses x.zip.abc123.part."""
    for suffix in PARTIAL:
        if path.name.endswith(suffix) and len(path.name) > len(suffix):
            stem = path.name[: -len(suffix)]
            parts = stem.rsplit(".", 2)
            if len(parts) == 3 and len(parts[2]) == 6 and parts[2].isalnum() and not parts[2].isdigit():
                stem = f"{parts[0]}.{parts[1]}"  # x.zip.Ab12cD.part
            return path.with_name(stem)
    return None


def hidden(path: Path) -> bool:
    return path.name.startswith(".") or path.name.startswith("~")


class Tracker:
    def __init__(self, keep_seconds: float = 1800) -> None:
        self.keep_seconds = keep_seconds
        self.items: dict[Path, Download] = {}

    def changed(self, path: Path, size: int, now: float | None = None) -> bool:
        """A partial file appeared or grew. True when the list changed in a visible way."""
        now = time.time() if now is None else now
        final = final_name(path)
        if final is None or hidden(final):
            return False
        item = self.items.get(final)
        if item is None or item.finished is not None:
            self.items[final] = Download(final, size, now)
            return True
        grew = abs(size - item.size) > 256 * 1024  # don't redraw for every few KB
        if grew:
            item.size = size
        return grew

    def done(self, partial: Path, final_exists: bool, size: int = 0, now: float | None = None) -> bool:
        """A partial file went away (renamed into place, or cancelled)."""
        now = time.time() if now is None else now
        final = final_name(partial)
        item = self.items.get(final) if final else None
        if item is None:
            return False
        if final_exists:
            item.finished = now
            item.size = size or item.size
        else:
            del self.items[final]  # cancelled
        return True

    def created(self, path: Path, size: int, now: float | None = None) -> bool:
        """A complete file was saved directly (no partial file): count it as finished at once."""
        now = time.time() if now is None else now
        if final_name(path) is not None or hidden(path) or size == 0:
            return False
        item = self.items.get(path)
        if item is not None and item.finished is None:
            return False  # the browser's placeholder next to the partial file
        if item is not None:
            item.size = size
            return False
        self.items[path] = Download(path, size, now, now)
        return True

    def forget(self, path: Path) -> None:
        self.items.pop(path, None)

    def prune(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        for path, item in list(self.items.items()):
            if item.finished is not None and now - item.finished > self.keep_seconds:
                del self.items[path]
            elif item.finished is None and now - item.started > 86400:
                del self.items[path]  # a partial file left behind by a crash

    def active(self) -> list[Download]:
        return sorted((d for d in self.items.values() if d.finished is None), key=lambda d: -d.started)

    def recent(self) -> list[Download]:
        return sorted((d for d in self.items.values() if d.finished is not None), key=lambda d: -(d.finished or 0))


def human_size(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") or size >= 100 else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def download_dir() -> Path:
    from gi.repository import GLib

    folder = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
    return Path(folder) if folder else Path.home() / "Downloads"


class DownloadWatcher:
    def __init__(self, bus, section: dict) -> None:
        self.bus = bus
        self.tracker = Tracker(float(section.get("keep_minutes", 30)) * 60)
        folder = section.get("folder") or ""
        self.folder = Path(folder).expanduser() if folder else download_dir()
        self.monitor = None

    def start(self) -> bool:
        from gi.repository import Gio

        if not self.folder.is_dir():
            return False
        try:
            self.monitor = Gio.File.new_for_path(str(self.folder)).monitor_directory(
                Gio.FileMonitorFlags.WATCH_MOVES, None)
        except Exception as exc:
            _log.info("Can't watch %s: %s", self.folder, exc)
            return False
        self.monitor.set_rate_limit(1000)
        self.monitor.connect("changed", self._event)
        # partial files already there (a download started before we did)
        for path in self.folder.iterdir():
            if final_name(path) is not None:
                self.tracker.changed(path, _size(path))
        return True

    def _event(self, _monitor, file, other, event) -> None:
        from gi.repository import Gio

        path = Path(file.get_path() or "")
        changed = False
        E = Gio.FileMonitorEvent
        if event in (E.CREATED, E.CHANGED, E.CHANGES_DONE_HINT):
            if final_name(path) is not None:
                changed = self.tracker.changed(path, _size(path))
            elif event == E.CHANGES_DONE_HINT or event == E.CREATED:
                changed = self.tracker.created(path, _size(path))
        elif event == E.RENAMED and other is not None:
            target = Path(other.get_path() or "")
            if final_name(path) is not None:
                changed = self.tracker.done(path, target.exists() and final_name(target) is None, _size(target))
            elif final_name(target) is not None:
                changed = self.tracker.changed(target, _size(target))
            else:
                changed = self.tracker.created(target, _size(target))
        elif event in (E.DELETED, E.MOVED_OUT):
            if final_name(path) is not None:
                final = final_name(path)
                changed = self.tracker.done(path, bool(final and final.exists()), _size(final) if final else 0)
            else:
                self.tracker.forget(path)
                changed = True
        if changed:
            self.tracker.prune()
            self.bus.publish("downloads.changed")


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0
