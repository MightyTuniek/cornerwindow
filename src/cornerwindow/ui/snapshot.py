"""Pictures of our own windows, drawn offscreen (no compositor or screen grab needed).

GTK draws a window into a cairo image exactly as it draws it on screen; the island clears to
transparent outside its outline, so the result can be laid over the wallpaper. Used by
`cornerwindow snapshot FILE.png` and by scripts/screenshots.py (the README's pictures).
"""

from __future__ import annotations

import cairo
from gi.repository import Gdk

SETTLE_S = 2.6  # long enough for the open spring and every card's staggered entrance


def render(window) -> cairo.ImageSurface:
    """The whole window as a transparent ARGB image at its device scale."""
    scale = window.get_scale_factor()
    width, height = window.get_allocated_width(), window.get_allocated_height()
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(1, width * scale), max(1, height * scale))
    surface.set_device_scale(scale, scale)
    window.draw(cairo.Context(surface))
    surface.flush()
    return surface


def origin(window) -> tuple[int, int]:
    """Top-left on screen, logical pixels."""
    gdk_window = window.get_window()
    if gdk_window is None:
        return 0, 0
    _ok, x, y = gdk_window.get_origin()
    return x, y


def compose(parts: list[tuple[cairo.ImageSurface, int, int]], crop: tuple[int, int, int, int], scale: int,
            backdrop: cairo.ImageSurface | None = None) -> cairo.ImageSurface:
    """Lay window images (with their screen positions) over the backdrop, cut out `crop`
    (logical x, y, w, h). The backdrop is a screen-sized image in device pixels."""
    cx, cy, cw, ch = crop
    out = cairo.ImageSurface(cairo.FORMAT_ARGB32, cw * scale, ch * scale)
    cr = cairo.Context(out)
    if backdrop is not None:
        cr.set_source_surface(backdrop, -cx * scale, -cy * scale)
        cr.paint()
    for surface, x, y in parts:
        cr.save()
        cr.translate((x - cx) * scale, (y - cy) * scale)
        cr.scale(scale, scale)
        cr.set_source_surface(surface, 0, 0)
        cr.paint()
        cr.restore()
    out.flush()
    return out


def wallpaper(_app=None) -> cairo.ImageSurface | None:
    """The desktop wallpaper as a screen-sized image in device pixels (what the glass bends)."""
    from ..platform.backdrop import _monitors, _screen_size
    from ..platform.backdrop.wallpaper import Wallpaper

    try:
        return Wallpaper(_screen_size, _monitors).surface()
    except Exception:
        return None


def panel_crop(island, margin: int = 56) -> tuple[int, int, int, int]:
    """The open panel plus a strip of wallpaper on its inner sides (the screen edges cut the rest)."""
    rect = island.panel_rect()
    monitor = island._monitor().get_geometry()
    left = island.corner.endswith("left")
    top = island.corner.startswith("top")
    x = rect.x if left else max(monitor.x, rect.x - margin)
    y = rect.y if top else max(monitor.y, rect.y - margin)
    right = rect.x + rect.width + (margin if left else 0)
    bottom = rect.y + rect.height + (margin if top else 0)
    right = min(right, monitor.x + monitor.width)
    bottom = min(bottom, monitor.y + monitor.height)
    return x, y, right - x, bottom - y


def save_island(app, path: str, what: str = "panel", extra: list | None = None,
                crop: tuple[int, int, int, int] | None = None) -> str:
    """Save the island over the wallpaper. what = "panel" (the panel and a strip of wallpaper) or
    "window" (the whole window). `extra`: more windows to draw (the settings window)."""
    island = app.island
    scale = island.get_scale_factor()
    parts = []
    for window in [*(extra or []), island]:
        if window is not None and window.get_visible():
            x, y = origin(window)
            parts.append((render(window), x, y))
    if crop is None:
        if what == "window":
            x, y = origin(island)
            crop = (x, y, island.get_allocated_width(), island.get_allocated_height())
        else:
            crop = panel_crop(island)
    compose(parts, crop, scale, wallpaper(app)).write_to_png(path)
    return path


def screen_size() -> tuple[int, int]:
    display = Gdk.Display.get_default()
    geometry = (display.get_primary_monitor() or display.get_monitor(0)).get_geometry()
    return geometry.width, geometry.height
