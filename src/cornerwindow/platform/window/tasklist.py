"""Open windows grouped by app, for the Apps card: the taskbar, inside the panel. X11 via libwnck.

Each window is matched to its installed app (.desktop file) by WM_CLASS, the way docks do it, so
it gets the app's real icon and name and lines up with the desktop's favourites. Apps keep the
order they first appeared in (favourites first), so icons never jump around while you switch.

Wnck signals drive everything; there is no polling. Changes are coalesced into one
`windows.changed` bus event per main-loop turn.

Never keep a Wnck.Window beyond the call that got it: when a window closes libwnck tears its
object down, and a reference we still hold would finalize it later, outside libwnck's own
bookkeeping, which crashes. Everything here keeps the X window id and looks the live window up.

On Wayland (native, not XWayland) Wnck sees nothing and `available()` is False.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from ...core import log

_log = log.get("tasklist")


# -- pure logic (tested) -------------------------------------------------------------------
def desktop_keys(desktop_id: str) -> list[str]:
    """Lookup keys for a .desktop id: "app.zen_browser.zen.desktop" -> ["app.zen_browser.zen", "zen"]."""
    stem = desktop_id[:-8] if desktop_id.endswith(".desktop") else desktop_id
    stem = stem.lower()
    keys = [stem]
    if "." in stem:
        keys.append(stem.rsplit(".", 1)[-1])
    return keys


def pick_app(candidates: Iterable[str], tables: list[dict[str, Any]]) -> Any:
    """The first table hit for any candidate, trying the most reliable table first."""
    names = [c.lower() for c in candidates if c]
    for table in tables:
        for name in names:
            if name in table:
                return table[name]
    return None


def cycle_target(order: list[Any], current: Any, delta: int) -> Any:
    """The window `delta` steps from `current` in `order` (wrapping); the first/last one when none is current."""
    if not order:
        return None
    if current not in order:
        return order[0] if delta > 0 else order[-1]
    return order[(order.index(current) + delta) % len(order)]


def group_order(keys_seen: list[str], favorites: list[str], running: set[str]) -> list[str]:
    """Favourites first (as the desktop orders them), then running apps by when they first appeared."""
    out = list(dict.fromkeys(favorites))
    out += [k for k in keys_seen if k in running and k not in out]
    return out


# -- model --------------------------------------------------------------------------------
@dataclass
class WindowInfo:
    xid: int
    title: str
    minimized: bool
    active: bool
    urgent: bool
    workspace: str | None  # e.g. "Workspace 2" when not on the current one


@dataclass
class AppGroup:
    key: str
    name: str
    icon: Any = None  # a Gio.Icon
    pixbuf: Any = None  # the window's own icon, when there is no app icon
    info: Any = None  # the Gio.DesktopAppInfo, if we found one
    favorite: bool = False
    windows: list[WindowInfo] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return any(w.active for w in self.windows)

    @property
    def urgent(self) -> bool:
        return any(w.urgent for w in self.windows)

    def signature(self) -> tuple:
        """What the strip draws (titles only show in tooltips and the window list)."""
        return (self.key, self.active, self.urgent, len(self.windows),
                tuple(w.minimized for w in self.windows))


class _AppIndex:
    """Installed apps, indexed every way a window can name its app."""

    def __init__(self) -> None:
        self.tables: list[dict[str, Any]] = [{}, {}, {}, {}]
        self.by_id: dict[str, Any] = {}

    def load(self) -> None:
        from gi.repository import Gio

        wmclass, ids, exes, names = {}, {}, {}, {}
        by_id = {}
        for info in Gio.AppInfo.get_all():
            if not isinstance(info, Gio.DesktopAppInfo):
                continue
            desktop_id = info.get_id() or ""
            by_id[desktop_id] = info
            if info.get_startup_wm_class():
                wmclass.setdefault(info.get_startup_wm_class().lower(), info)
            for key in desktop_keys(desktop_id):
                ids.setdefault(key, info)
            exe = (info.get_executable() or "").rsplit("/", 1)[-1].lower()
            if exe and exe not in ("flatpak", "env", "sh", "bash", "snap", "gio"):
                exes.setdefault(exe, info)
            if info.get_name():
                names.setdefault(info.get_name().lower(), info)
        self.tables, self.by_id = [wmclass, ids, exes, names], by_id


class Tasklist:
    """The window list service (`ctx.services.windows`)."""

    def __init__(self, bus, section: dict) -> None:
        self.bus = bus
        self.config = section
        self._screen = None
        self._index = _AppIndex()
        self._index_stale = True
        self._first_seen: dict[str, int] = {}  # group key -> order of appearance
        self._window_seen: dict[int, int] = {}  # xid -> order of appearance
        self._counter = 0
        self._pending = 0
        self.last_active: int | None = None  # xid of the last focused window that isn't ours
        self._own_pid = os.getpid()
        try:
            from .x11 import Wnck
            from gi.repository import Gdk

            if Wnck is not None and "X11" in type(Gdk.Display.get_default()).__name__:
                self._screen = Wnck.Screen.get_default()
        except Exception as exc:
            _log.debug("No window list: %s", exc)
        if self._screen is None:
            return
        self._screen.force_update()
        self._screen.connect("window-opened", self._opened)
        self._screen.connect("window-closed", self._closed)
        for signal in ("active-window-changed", "active-workspace-changed", "showing-desktop-changed"):
            self._screen.connect(signal, self._screen_changed)
        for window in self._screen.get_windows():
            self._watch(window)
        self._track_active()
        try:
            from gi.repository import Gio

            self._apps_monitor = Gio.AppInfoMonitor.get()
            self._apps_monitor.connect("changed", lambda *_a: self._invalidate_index())
            self._favorites = Gio.Settings.new("org.gnome.shell") \
                if Gio.SettingsSchemaSource.get_default().lookup("org.gnome.shell", True) else None
            if self._favorites is not None:
                self._favorites.connect("changed::favorite-apps", lambda *_a: self._queue())
        except Exception:
            self._favorites = None

    def available(self) -> bool:
        return self._screen is not None

    def reconfigure(self, section: dict) -> None:
        self.config = section
        self._queue()

    # -- signals --------------------------------------------------------------------------
    def _watch(self, window) -> None:
        xid = window.get_xid()
        if xid in self._window_seen:
            return
        self._window_seen[xid] = self._tick()
        for signal in ("name-changed", "state-changed", "icon-changed", "workspace-changed", "class-changed"):
            window.connect(signal, lambda *_a: self._queue())

    def _opened(self, _screen, window) -> None:
        self._watch(window)
        self._queue()

    def _closed(self, _screen, window) -> None:
        xid = window.get_xid()
        self._window_seen.pop(xid, None)
        if self.last_active == xid:
            self.last_active = None
        self._track_active()
        self._queue()

    def _screen_changed(self, *_args) -> None:
        self._track_active()
        self._queue()

    def _track_active(self) -> None:
        active = self._screen.get_active_window()
        if active is not None and self._listed(active):
            self.last_active = active.get_xid()

    def _tick(self) -> int:
        self._counter += 1
        return self._counter

    def _queue(self) -> None:
        if self._pending:
            return
        from gi.repository import GLib

        def fire() -> bool:
            self._pending = 0
            self.bus.publish("windows.changed")
            return False

        self._pending = GLib.idle_add(fire)

    def _invalidate_index(self) -> None:
        self._index_stale = True
        self._queue()

    # -- queries --------------------------------------------------------------------------
    def _listed(self, window) -> bool:
        from .x11 import Wnck

        if window.is_skip_tasklist() or window.get_pid() == self._own_pid:
            return False
        return window.get_window_type() in (Wnck.WindowType.NORMAL, Wnck.WindowType.DIALOG,
                                            Wnck.WindowType.UTILITY)

    def favorites(self) -> list[str]:
        if self._favorites is None or not self.config.get("favorites", True):
            return []
        return list(self._favorites.get_strv("favorite-apps"))

    def groups(self) -> list[AppGroup]:
        """Apps with their windows, in strip order."""
        if self._screen is None:
            return []
        if self._index_stale:
            self._index.load()  # ~10 ms, only after apps were (un)installed
            self._index_stale = False
        workspace = self._screen.get_active_workspace()
        groups: dict[str, AppGroup] = {}
        windows = sorted((w for w in self._screen.get_windows() if self._listed(w)),
                         key=lambda w: self._window_seen.get(w.get_xid(), 1 << 30))
        active_xid = self.last_active
        for window in windows:
            info = pick_app([window.get_class_group_name() or "", window.get_class_instance_name() or ""],
                            self._index.tables)
            key = (info.get_id() if info is not None else (window.get_class_group_name() or window.get_name())).lower()
            group = groups.get(key)
            if group is None:
                group = groups[key] = AppGroup(
                    key=key, name=(info.get_name() if info is not None else window.get_class_group_name())
                    or window.get_name(), icon=info.get_icon() if info is not None else None, info=info)
                self._first_seen.setdefault(key, self._tick())
            if group.icon is None and group.pixbuf is None:
                group.pixbuf = window.get_icon()
            ws = window.get_workspace()
            other = ws is not None and workspace is not None and ws != workspace
            group.windows.append(WindowInfo(
                xid=window.get_xid(), title=window.get_name() or group.name, minimized=window.is_minimized(),
                active=window.get_xid() == active_xid, urgent=window.needs_attention(),
                workspace=(ws.get_name() if other else None)))
        favorites = []
        for desktop_id in self.favorites():
            key = desktop_id.lower()
            if key not in groups:
                info = self._index.by_id.get(desktop_id)
                if info is None:
                    continue
                groups[key] = AppGroup(key=key, name=info.get_name(), icon=info.get_icon(), info=info)
            groups[key].favorite = True
            favorites.append(key)
        seen = sorted(self._first_seen, key=self._first_seen.get)
        order = group_order(seen, favorites, {k for k, g in groups.items() if g.windows})
        return [groups[k] for k in order]

    def find(self, query: str, limit: int = 3) -> list[tuple[AppGroup, WindowInfo]]:
        """Open windows whose app name or title matches `query` (for the command bar)."""
        from ...core.launcher import AppEntry, score

        scored = []
        for group in self.groups():
            for window in group.windows:
                value = max(score(query, AppEntry(group.name, [])),
                            score(query, AppEntry(window.title, [])) * 0.9)
                if value >= 50:  # prefix and substring matches only: titles are long
                    scored.append((value, window.active, group, window))
        scored.sort(key=lambda s: (-s[0], s[1]))  # the window you're in last: you want another one
        return [(g, w) for _v, _a, g, w in scored[:limit]]

    def urgent(self) -> list[WindowInfo]:
        return [w for g in self.groups() for w in g.windows if w.urgent]

    # -- actions --------------------------------------------------------------------------
    def _time(self) -> int:
        from gi.repository import Gdk, GdkX11, Gtk

        stamp = Gtk.get_current_event_time()
        if stamp:
            return stamp
        root = Gdk.get_default_root_window()
        return GdkX11.x11_get_server_time(root) if isinstance(root, GdkX11.X11Window) else 0

    def _live(self, window: WindowInfo):
        """The Wnck.Window for `window` if it is still open (look it up; never store it)."""
        if self._screen is None:
            return None
        return next((w for w in self._screen.get_windows() if w.get_xid() == window.xid), None)

    def activate(self, window: WindowInfo) -> None:
        stamp = self._time()
        ref = self._live(window)
        if ref is None:
            return
        try:
            ws = ref.get_workspace()
            if ws is not None and ws != self._screen.get_active_workspace():
                ws.activate(stamp)
            ref.activate(stamp)
        except Exception as exc:  # it may have closed meanwhile
            _log.debug("Could not activate %s: %s", window.title, exc)

    def minimize(self, window: WindowInfo) -> None:
        ref = self._live(window)
        if ref is None:
            return
        try:
            ref.minimize()
        except Exception as exc:
            _log.debug("Could not minimise %s: %s", window.title, exc)

    def close(self, window: WindowInfo) -> None:
        ref = self._live(window)
        if ref is None:
            return
        try:
            ref.close(self._time())
        except Exception as exc:
            _log.debug("Could not close %s: %s", window.title, exc)

    def launch(self, group: AppGroup) -> bool:
        """Open the app (a new window when it already runs)."""
        if group.info is None:
            return False
        from gi.repository import Gdk

        display = Gdk.Display.get_default()
        try:
            group.info.launch([], display.get_app_launch_context() if display else None)
        except Exception as exc:
            _log.warning("Could not open %s: %s", group.name, exc)
            return False
        return True

    def cycle(self, delta: int = 1) -> WindowInfo | None:
        """Focus the next (or previous) window, in strip order."""
        order = [w for g in self.groups() for w in g.windows]
        current = next((w for w in order if w.active), None)
        step = 1 if delta >= 0 else -1
        target = cycle_target([w.xid for w in order], current.xid if current else None, step)
        window = next((w for w in order if w.xid == target), None)
        if window is not None:
            self.activate(window)
        return window

    def show_desktop(self, on: bool | None = None) -> bool:
        if self._screen is None:
            return False
        if on is None:
            on = not self._screen.get_showing_desktop()
        self._screen.toggle_showing_desktop(bool(on))
        return bool(on)

    def subscribe(self, fn: Callable[[], None]) -> Callable[[], None]:
        return self.bus.subscribe("windows.changed", fn)

    # -- placing windows (the snap commands) ----------------------------------------------
    def target(self):
        """The window you were in before opening the panel (a live Wnck.Window), or None."""
        if self._screen is None or self.last_active is None:
            return None
        return next((w for w in self._screen.get_windows() if w.get_xid() == self.last_active), None)

    @staticmethod
    def work_areas() -> list[tuple[int, int, int, int]]:
        from gi.repository import Gdk

        display = Gdk.Display.get_default()
        out = []
        for i in range(display.get_n_monitors()):
            area = display.get_monitor(i).get_workarea()
            out.append((area.x, area.y, area.width, area.height))
        return out

    def snap(self, name: str, gap: int = 0) -> str:
        """Move the last active window to a layout ("left", "top-right", "max", "center",
        "next-monitor", "restore"). Returns feedback text."""
        from ...core import snap as geometry
        from .x11 import Wnck

        window = self.target()
        if window is None:
            return "No window to move"
        layout = geometry.resolve(name)
        if layout is None:
            return f"Unknown layout {name!r}"
        xid = window.get_xid()
        current = tuple(window.get_client_window_geometry())
        areas = self.work_areas() or [current]
        cx, cy = current[0] + current[2] // 2, current[1] + current[3] // 2
        index = next((i for i, a in enumerate(areas) if geometry.contains(a, cx, cy)), 0)
        work = areas[index]
        saved = self.__dict__.setdefault("_saved", {})  # xid -> geometry before our first snap
        placed = self.__dict__.setdefault("_placed", {})  # xid -> where we last put it
        if layout == "restore":
            if xid not in saved:
                return "Nothing to restore"
            rect = saved.pop(xid)
        elif layout == "next-monitor":
            if len(areas) < 2:
                return "Only one monitor"
            rect = geometry.to_monitor(current, work, areas[(index + 1) % len(areas)])
        elif layout == "center":
            rect = geometry.centered(current, work)
        else:
            rect = geometry.layout_rect(layout, work, gap)
        if layout != "restore" and not _near(current, placed.get(xid)):
            saved[xid] = current  # snapping again from a snapped place keeps the original
        placed[xid] = rect
        try:
            if window.is_maximized():
                window.unmaximize()
            if window.is_minimized():
                window.unminimize(self._time())
            if layout == "max":
                window.maximize()
            else:
                mask = (Wnck.WindowMoveResizeMask.X | Wnck.WindowMoveResizeMask.Y
                        | Wnck.WindowMoveResizeMask.WIDTH | Wnck.WindowMoveResizeMask.HEIGHT)
                window.set_geometry(Wnck.WindowGravity.STATIC, mask, *rect)
            window.activate(self._time())
        except Exception as exc:
            _log.debug("Could not move %s: %s", window.get_name(), exc)
            return "That window can't be moved"
        return {"next-monitor": "Moved to the next monitor", "restore": "Restored", "center": "Centred",
                "max": "Maximised"}.get(layout, layout.replace("-", " ").capitalize())


def _near(a, b, slack: int = 40) -> bool:
    return b is not None and all(abs(x - y) <= slack for x, y in zip(a, b))
