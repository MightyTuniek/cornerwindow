"""Window layouts as pure geometry: where "left half" or "top-right quarter" lands in a work area.

A rect is (x, y, width, height) in screen pixels; the work area is the monitor minus panels.
"""

from __future__ import annotations

Rect = tuple[int, int, int, int]

# name -> (x, y, w, h) as fractions of the work area
LAYOUTS: dict[str, tuple[float, float, float, float]] = {
    "left": (0, 0, 0.5, 1), "right": (0.5, 0, 0.5, 1),
    "top": (0, 0, 1, 0.5), "bottom": (0, 0.5, 1, 0.5),
    "top-left": (0, 0, 0.5, 0.5), "top-right": (0.5, 0, 0.5, 0.5),
    "bottom-left": (0, 0.5, 0.5, 0.5), "bottom-right": (0.5, 0.5, 0.5, 0.5),
    "left-third": (0, 0, 1 / 3, 1), "center-third": (1 / 3, 0, 1 / 3, 1), "right-third": (2 / 3, 0, 1 / 3, 1),
    "left-two-thirds": (0, 0, 2 / 3, 1), "right-two-thirds": (1 / 3, 0, 2 / 3, 1),
    "max": (0, 0, 1, 1),
}
ALIASES = {
    "l": "left", "r": "right", "t": "top", "b": "bottom", "tl": "top-left", "tr": "top-right",
    "bl": "bottom-left", "br": "bottom-right", "maximize": "max", "maximise": "max", "full": "max",
    "c": "center", "centre": "center", "middle": "center", "third": "center-third",
    "lt": "left-third", "rt": "right-third", "ct": "center-third", "l2": "left-two-thirds",
    "r2": "right-two-thirds", "next": "next-monitor", "monitor": "next-monitor", "screen": "next-monitor",
    "undo": "restore", "back": "restore",
}
SPECIAL = ("center", "next-monitor", "restore")


def resolve(name: str) -> str | None:
    name = name.strip().lower().replace(" ", "-").replace("_", "-")
    name = ALIASES.get(name, name)
    if name in LAYOUTS or name in SPECIAL:
        return name
    matches = [n for n in (*LAYOUTS, *SPECIAL) if n.startswith(name)] if name else []
    return matches[0] if len(matches) == 1 else None


def layout_rect(name: str, work: Rect, gap: int = 0) -> Rect:
    fx, fy, fw, fh = LAYOUTS[name]
    x, y, w, h = work
    left, top = x + round(w * fx), y + round(h * fy)
    right, bottom = x + round(w * (fx + fw)), y + round(h * (fy + fh))
    # a gap around the outside and half a gap between neighbours
    g_left = gap if fx == 0 else gap // 2
    g_top = gap if fy == 0 else gap // 2
    g_right = gap if fx + fw >= 0.999 else gap // 2
    g_bottom = gap if fy + fh >= 0.999 else gap // 2
    return left + g_left, top + g_top, right - left - g_left - g_right, bottom - top - g_top - g_bottom


def centered(window: Rect, work: Rect, scale: float = 0.7) -> Rect:
    """Centre the window; if it's nearly as big as the screen, shrink it to `scale` of the work area."""
    _x, _y, w, h = window
    wx, wy, ww, wh = work
    if w > ww * 0.92 or h > wh * 0.92:
        w, h = round(ww * scale), round(wh * scale)
    w, h = min(w, ww), min(h, wh)
    return wx + (ww - w) // 2, wy + (wh - h) // 2, w, h


def to_monitor(window: Rect, src: Rect, dst: Rect) -> Rect:
    """Same relative place and size on another work area."""
    x, y, w, h = window
    sx, sy, sw, sh = src
    dx, dy, dw, dh = dst
    fx, fy = (x - sx) / max(sw, 1), (y - sy) / max(sh, 1)
    fw, fh = w / max(sw, 1), h / max(sh, 1)
    nw, nh = min(dw, round(fw * dw)), min(dh, round(fh * dh))
    nx = min(max(dx, dx + round(fx * dw)), dx + dw - nw)
    ny = min(max(dy, dy + round(fy * dh)), dy + dh - nh)
    return nx, ny, nw, nh


def contains(rect: Rect, px: int, py: int) -> bool:
    x, y, w, h = rect
    return x <= px < x + w and y <= py < y + h
