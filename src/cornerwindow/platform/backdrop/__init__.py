"""What is behind our windows, for the Liquid Glass theme.

    backdrop = services.backdrop          # picks the best source on first use
    track = backdrop.track(xid_fn, area_fn, on_dirty)
    pixels = track.capture((x, y, w, h))  # root coordinates, device pixels
    track.untrack()

`capture(rect, refresh=True, sharp=True)` returns
(address, width, height, stride, origin_x, origin_y, generation, small): ARGB32
pixels of a rectangle containing the one asked for. `generation` changes whenever
the pixels did. `small` is None, or (address, w, h, stride) of a quarter-size,
4x4-averaged copy; with sharp=False a source may return only that, and then
`address` and `stride` are 0 (frosted glass never reads the sharp pixels).
`on_dirty` is called when something under `area_fn()` changed. Sources:

  x11        live: windows below ours composited over the wallpaper (X11 + Composite)
  wallpaper  static: just the wallpaper (Wayland, no compositor, or X11 failure)
"""

from __future__ import annotations

from gi.repository import Gdk

from ...core import log
from .wallpaper import Wallpaper, WallpaperBackdrop

_log = log.get("backdrop")


def _screen_size() -> tuple[int, int]:
    display = Gdk.Display.get_default()
    x0 = y0 = 0
    x1 = y1 = 1
    for i in range(display.get_n_monitors()):
        mon = display.get_monitor(i)
        g, s = mon.get_geometry(), mon.get_scale_factor()
        x0, y0 = min(x0, g.x * s), min(y0, g.y * s)
        x1, y1 = max(x1, (g.x + g.width) * s), max(y1, (g.y + g.height) * s)
    return x1 - x0, y1 - y0


def _monitors() -> list[tuple[int, int, int, int]]:
    display = Gdk.Display.get_default()
    result = []
    for i in range(display.get_n_monitors()):
        mon = display.get_monitor(i)
        g, s = mon.get_geometry(), mon.get_scale_factor()
        result.append((g.x * s, g.y * s, g.width * s, g.height * s))
    return result


def create(live: bool = True):
    wallpaper = Wallpaper(_screen_size, _monitors)
    display = Gdk.Display.get_default()
    display.connect("monitor-added", lambda *_a: wallpaper.screen_changed())
    display.connect("monitor-removed", lambda *_a: wallpaper.screen_changed())
    if live and type(display).__name__ == "X11Display" and Gdk.Screen.get_default().is_composited():
        try:
            from .x11 import X11Backdrop

            return X11Backdrop(wallpaper)
        except Exception as exc:
            _log.warning("Live backdrop unavailable (%s); using the wallpaper", exc)
    return WallpaperBackdrop(wallpaper)


def describe() -> tuple[str, str]:
    """(source, detail) for `cornerwindow doctor`, without opening windows."""
    import os

    session = os.environ.get("XDG_SESSION_TYPE", "")
    if session == "wayland" and os.environ.get("GDK_BACKEND") != "x11":
        return "wallpaper", "Wayland: windows behind the panel can't be read, glass shows the wallpaper"
    try:
        import ctypes

        for name in ("libX11.so.6", "libXcomposite.so.1", "libXdamage.so.1", "libXrender.so.1", "libXext.so.6"):
            ctypes.CDLL(name)
    except OSError as exc:
        return "wallpaper", f"missing X library: {exc}"
    return "x11", "live: windows below are composited with XComposite/XDamage"
