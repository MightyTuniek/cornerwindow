"""The desktop wallpaper as a screen-sized cairo surface.

GNOME Shell draws the wallpaper itself, not as an X window, so the live X11
backdrop composites windows on top of this. On Wayland or without a compositor
it is the whole backdrop (static, but correct for an empty desktop).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

import cairo

from ...core import log

_log = log.get("wallpaper")
SCHEMA = "org.gnome.desktop.background"

Rect = tuple[int, int, int, int]


def placement(img_w: int, img_h: int, area_w: int, area_h: int, option: str) -> tuple[float, float, float, float]:
    """(scale_x, scale_y, offset_x, offset_y) to draw an image into an area, GNOME style."""
    if img_w <= 0 or img_h <= 0:
        return 1.0, 1.0, 0.0, 0.0
    if option == "stretched":
        sx, sy = area_w / img_w, area_h / img_h
    elif option in ("zoom", "spanned"):
        sx = sy = max(area_w / img_w, area_h / img_h)
    elif option == "scaled":
        sx = sy = min(area_w / img_w, area_h / img_h)
    else:  # centered, wallpaper (tiled from the centre), none
        sx = sy = 1.0
    return sx, sy, (area_w - img_w * sx) / 2.0, (area_h - img_h * sy) / 2.0


def parse_color(value: str, fallback=(0.1, 0.1, 0.12)) -> tuple[float, float, float]:
    match = re.fullmatch(r"#?([0-9a-fA-F]{6})", (value or "").strip())
    if not match:
        return fallback
    raw = match.group(1)
    return tuple(int(raw[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def uri_to_path(uri: str) -> Path | None:
    if not uri:
        return None
    if uri.startswith("file://"):
        return Path(unquote(urlparse(uri).path))
    if uri.startswith("/"):
        return Path(uri)
    return None


def _slideshow_image(path: Path) -> Path | None:
    """GNOME's time-of-day slideshows are XML; use their first picture."""
    try:
        match = re.search(r"<file>\s*([^<]+?)\s*</file>", path.read_text(errors="replace"))
    except OSError:
        return None
    return Path(match.group(1)) if match else None


class Wallpaper:
    def __init__(self, screen_size: Callable[[], tuple[int, int]], monitors: Callable[[], list[Rect]]) -> None:
        self._screen_size = screen_size
        self._monitors = monitors
        self._surface: cairo.ImageSurface | None = None
        self._key = None
        self._listeners: list[Callable[[], None]] = []
        self._bg = None
        self._iface = None
        try:
            from gi.repository import Gio

            source = Gio.SettingsSchemaSource.get_default()
            if source and source.lookup(SCHEMA, True):
                self._bg = Gio.Settings.new(SCHEMA)
                self._bg.connect("changed", lambda *_a: self._changed())
            if source and source.lookup("org.gnome.desktop.interface", True):
                self._iface = Gio.Settings.new("org.gnome.desktop.interface")
                self._iface.connect("changed::color-scheme", lambda *_a: self._changed())
        except Exception as exc:
            _log.debug("No GSettings wallpaper: %s", exc)

    def on_change(self, fn: Callable[[], None]) -> None:
        self._listeners.append(fn)

    def _changed(self) -> None:
        self._surface = None
        self._key = None
        for fn in list(self._listeners):
            fn()

    def screen_changed(self) -> None:
        self._changed()

    def _settings(self) -> tuple[Path | None, str, tuple[float, float, float]]:
        if self._bg is None:
            return None, "zoom", (0.1, 0.1, 0.12)
        dark = bool(self._iface) and self._iface.get_string("color-scheme") == "prefer-dark"
        uri = ""
        if dark and "picture-uri-dark" in self._bg.props.settings_schema.list_keys():
            uri = self._bg.get_string("picture-uri-dark")
        uri = uri or self._bg.get_string("picture-uri")
        option = self._bg.get_string("picture-options")
        color = parse_color(self._bg.get_string("primary-color"))
        path = uri_to_path(uri)
        if path is not None and path.suffix.lower() == ".xml":
            path = _slideshow_image(path)
        return path, option, color

    def surface(self) -> cairo.ImageSurface | None:
        """Screen-sized ARGB32 surface (cached until the wallpaper or screen changes)."""
        width, height = self._screen_size()
        path, option, color = self._settings()
        key = (width, height, str(path), option, color, tuple(self._monitors()))
        if self._surface is not None and key == self._key:
            return self._surface
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(width, 1), max(height, 1))
        cr = cairo.Context(surface)
        cr.set_source_rgb(*color)
        cr.paint()
        pixbuf = None
        if path is not None and option != "none":
            try:
                from gi.repository import GdkPixbuf

                pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(path))
            except Exception as exc:
                _log.debug("Cannot load wallpaper %s: %s", path, exc)
        if pixbuf is not None:
            from gi.repository import Gdk

            areas = [(0, 0, width, height)] if option == "spanned" else (self._monitors() or [(0, 0, width, height)])
            for ax, ay, aw, ah in areas:
                sx, sy, ox, oy = placement(pixbuf.get_width(), pixbuf.get_height(), aw, ah, option)
                cr.save()
                cr.rectangle(ax, ay, aw, ah)
                cr.clip()
                cr.translate(ax + ox, ay + oy)
                cr.scale(sx, sy)
                Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
                if option == "wallpaper":
                    cr.get_source().set_extend(cairo.EXTEND_REPEAT)
                cr.get_source().set_filter(cairo.FILTER_GOOD)
                cr.paint()
                cr.restore()
        surface.flush()
        self._surface, self._key = surface, key
        return surface


class WallpaperBackdrop:
    """Backdrop that is only the wallpaper (Wayland, or X11 without Composite)."""

    name = "wallpaper"
    live = False

    def __init__(self, wallpaper: Wallpaper) -> None:
        self.wallpaper = wallpaper
        self.tracks: list[_WallTrack] = []
        wallpaper.on_change(self._changed)

    def _changed(self) -> None:
        for track in list(self.tracks):
            track.generation += 1
            track.on_dirty()

    def track(self, xid_fn, area_fn, on_dirty) -> "_WallTrack":
        track = _WallTrack(self, area_fn, on_dirty)
        self.tracks.append(track)
        return track

    def close(self) -> None:
        self.tracks.clear()


class _WallTrack:
    def __init__(self, owner: WallpaperBackdrop, area_fn, on_dirty) -> None:
        self.owner = owner
        self.area_fn = area_fn
        self.on_dirty = on_dirty
        self.generation = 1
        self.dirty = False

    def untrack(self) -> None:
        if self in self.owner.tracks:
            self.owner.tracks.remove(self)

    def invalidate(self) -> None:
        self.generation += 1
        self.on_dirty()

    def capture(self, rect: Rect, refresh: bool = True):
        surface = self.owner.wallpaper.surface()
        if surface is None:
            return None
        import ctypes

        data = surface.get_data()
        base = ctypes.addressof((ctypes.c_char * len(data)).from_buffer(data))
        self._keep = (surface, data)
        return (base, surface.get_width(), surface.get_height(), surface.get_stride(), 0, 0, self.generation)
