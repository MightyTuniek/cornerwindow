"""Icon painting for custom-drawn widgets.

Symbolic theme icons (Adwaita names exist on practically every distro) are
recoloured on the fly; a few glyphs are drawn by hand so they look identical
everywhere and can animate (e.g. the sun's rays follow brightness).
"""

from __future__ import annotations

import math
from functools import lru_cache

from gi.repository import Gdk, Gtk

FALLBACKS = {
    "brightness": ("display-brightness-symbolic", "weather-clear-symbolic"),
    "volume": ("audio-volume-high-symbolic",),
    "volume-muted": ("audio-volume-muted-symbolic",),
    "mic": ("audio-input-microphone-symbolic", "microphone-sensitive-symbolic"),
    "mic-off": ("microphone-disabled-symbolic", "audio-input-microphone-muted-symbolic"),
    "wifi": ("network-wireless-signal-excellent-symbolic", "network-wireless-symbolic"),
    "wifi-off": ("network-wireless-offline-symbolic", "network-wireless-disabled-symbolic"),
    "bluetooth": ("bluetooth-active-symbolic", "bluetooth-symbolic"),
    "night": ("night-light-symbolic", "weather-clear-night-symbolic"),
    "dark": ("weather-clear-night-symbolic", "night-light-symbolic"),
    "dnd": ("notifications-disabled-symbolic", "preferences-system-notifications-symbolic"),
    "caffeine": ("my-caffeine-on-symbolic", "preferences-desktop-screensaver-symbolic"),
    "lock": ("system-lock-screen-symbolic", "changes-prevent-symbolic"),
    "suspend": ("weather-clear-night-symbolic", "media-playback-pause-symbolic"),
    "reboot": ("system-reboot-symbolic", "view-refresh-symbolic"),
    "poweroff": ("system-shutdown-symbolic",),
    "logout": ("system-log-out-symbolic",),
    "play": ("media-playback-start-symbolic",),
    "pause": ("media-playback-pause-symbolic",),
    "next": ("media-skip-forward-symbolic",),
    "previous": ("media-skip-backward-symbolic",),
    "pin": ("view-pin-symbolic", "pin-symbolic", "emblem-favorite-symbolic"),
    "settings": ("emblem-system-symbolic", "preferences-system-symbolic"),
    "copy": ("edit-copy-symbolic",),
    "clear": ("edit-clear-all-symbolic", "edit-delete-symbolic"),
    "timer": ("alarm-symbolic", "preferences-system-time-symbolic"),
    "music": ("audio-x-generic-symbolic",),
    "speaker": ("audio-speakers-symbolic", "audio-volume-high-symbolic"),
    "monitor": ("video-display-symbolic",),
    "headphones": ("audio-headphones-symbolic",),
    "search": ("system-search-symbolic",),
    "weather": ("weather-few-clouds-symbolic",),
}

DRAWN = {"sun", "contrast", "caffeine-cup", "glass"}


@lru_cache(maxsize=256)
def _pixbuf(name: str, size: int, rgba: tuple[float, float, float, float], scale: int):
    theme = Gtk.IconTheme.get_default()
    for candidate in FALLBACKS.get(name, (name,)):
        info = theme.lookup_icon_for_scale(candidate, size, scale, Gtk.IconLookupFlags.FORCE_SIZE)
        if info is None:
            continue
        color = Gdk.RGBA(*rgba)
        try:
            pixbuf, _ = info.load_symbolic(color, None, None, None)
            return pixbuf
        except Exception:
            continue
    return None


def paint(cr, name: str, cx: float, cy: float, size: float, rgba=(1, 1, 1, 1), scale: int = 1,
          amount: float = 1.0) -> None:
    """Paint icon `name` centred at (cx, cy)."""
    if name == "sun":
        _sun(cr, cx, cy, size, rgba, amount)
        return
    if name == "contrast":
        _contrast(cr, cx, cy, size, rgba)
        return
    if name == "caffeine-cup":
        _cup(cr, cx, cy, size, rgba)
        return
    if name == "glass":
        _droplet(cr, cx, cy, size, rgba)
        return
    pixbuf = _pixbuf(name, int(size), tuple(rgba), scale)
    if pixbuf is None:
        cr.set_source_rgba(*rgba)
        cr.arc(cx, cy, size * 0.18, 0, 2 * math.pi)
        cr.fill()
        return
    cr.save()
    cr.translate(cx - size / 2, cy - size / 2)
    if scale != 1:
        cr.scale(1 / scale, 1 / scale)
    Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
    cr.paint()
    cr.restore()


def _sun(cr, cx, cy, size, rgba, amount) -> None:
    cr.save()
    cr.set_source_rgba(*rgba)
    core = size * 0.2
    cr.arc(cx, cy, core, 0, 2 * math.pi)
    cr.fill()
    cr.set_line_width(max(size * 0.1, 1.4))
    cr.set_line_cap(1)  # ROUND
    ray = size * (0.08 + 0.12 * max(0.0, min(1.0, amount)))
    start = core + size * 0.1
    for i in range(8):
        angle = i * math.pi / 4
        cr.move_to(cx + math.cos(angle) * start, cy + math.sin(angle) * start)
        cr.line_to(cx + math.cos(angle) * (start + ray), cy + math.sin(angle) * (start + ray))
    cr.stroke()
    cr.restore()


def _contrast(cr, cx, cy, size, rgba) -> None:
    cr.save()
    r = size * 0.38
    cr.set_source_rgba(*rgba)
    cr.set_line_width(max(size * 0.09, 1.4))
    cr.arc(cx, cy, r, 0, 2 * math.pi)
    cr.stroke()
    cr.arc(cx, cy, r, -math.pi / 2, math.pi / 2)
    cr.close_path()
    cr.fill()
    cr.restore()


def _cup(cr, cx, cy, size, rgba) -> None:
    cr.save()
    cr.set_source_rgba(*rgba)
    cr.set_line_width(max(size * 0.09, 1.4))
    w, h = size * 0.5, size * 0.42
    x0, y0 = cx - w * 0.6, cy - h * 0.25
    cr.move_to(x0, y0)
    cr.line_to(x0 + w, y0)
    cr.line_to(x0 + w * 0.88, y0 + h)
    cr.line_to(x0 + w * 0.12, y0 + h)
    cr.close_path()
    cr.fill()
    cr.arc(x0 + w + size * 0.03, y0 + h * 0.4, size * 0.11, -math.pi / 2, math.pi / 2)
    cr.stroke()
    for i in range(2):
        sx = x0 + w * (0.32 + 0.36 * i)
        cr.move_to(sx, y0 - size * 0.08)
        cr.curve_to(sx - size * 0.06, y0 - size * 0.16, sx + size * 0.06, y0 - size * 0.22, sx, y0 - size * 0.3)
        cr.stroke()
    cr.restore()


def _droplet(cr, cx, cy, size, rgba) -> None:
    """A drop of liquid glass: outlined, with a highlight arc inside."""
    cr.save()
    cr.set_source_rgba(*rgba)
    cr.set_line_width(max(size * 0.09, 1.4))
    r = size * 0.27
    by = cy + size * 0.14
    top = cy - size * 0.42
    cr.move_to(cx, top)
    cr.curve_to(cx + r * 0.35, top + r * 0.9, cx + r, by - r * 0.9, cx + r, by)
    cr.arc(cx, by, r, 0, math.pi)
    cr.curve_to(cx - r, by - r * 0.9, cx - r * 0.35, top + r * 0.9, cx, top)
    cr.close_path()
    cr.stroke()
    cr.set_line_cap(1)
    cr.arc(cx, by, r * 0.55, math.pi * 0.55, math.pi * 0.95)
    cr.stroke()
    cr.restore()
