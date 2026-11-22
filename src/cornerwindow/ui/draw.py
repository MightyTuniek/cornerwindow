"""Cairo/Pango drawing helpers shared by custom widgets."""

from __future__ import annotations

import math

import gi

gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo  # noqa: E402

FONT = "Inter, SF Pro Text, Cantarell, sans-serif"
# The black theme's colours; widgets should read ui.theme.current() instead.
TEXT = (0.96, 0.96, 0.97, 1.0)
TEXT_DIM = (1.0, 1.0, 1.0, 0.55)
TRACK = (1.0, 1.0, 1.0, 0.13)


def rounded_rect(cr, x: float, y: float, w: float, h: float, r: float) -> None:
    r = max(0.0, min(r, w / 2, h / 2))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def pill(cr, x: float, y: float, w: float, h: float) -> None:
    rounded_rect(cr, x, y, w, h, h / 2)


def platter_rim(cr, alpha: float, top: float, height: float) -> None:
    """Liquid Glass hairline on a control (the current path): bright at the top,
    fading down, like light catching the rim. Keeps the path."""
    if alpha <= 0.002:
        return
    import cairo

    gradient = cairo.LinearGradient(0, top, 0, top + height)
    gradient.add_color_stop_rgba(0.0, 1, 1, 1, alpha)
    gradient.add_color_stop_rgba(0.5, 1, 1, 1, alpha * 0.18)
    gradient.add_color_stop_rgba(1.0, 1, 1, 1, alpha * 0.45)
    cr.set_source(gradient)
    cr.set_line_width(1.0)
    cr.stroke_preserve()


def mix(a, b, t: float):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def text(cr, widget, value: str, x: float, y: float, size: float = 12, weight: int = 500,
         rgba=None, align: str = "left", valign: str = "center", tabular: bool = True) -> tuple[float, float]:
    """Draw text; (x, y) is the anchor given by align/valign. Returns (width, height)."""
    layout = PangoCairo.create_layout(cr)
    desc = Pango.FontDescription.from_string(f"{FONT} {size}px")
    desc.set_absolute_size(size * Pango.SCALE)
    desc.set_weight(Pango.Weight(weight))
    layout.set_font_description(desc)
    if tabular:
        try:
            attrs = Pango.AttrList()
            attrs.insert(Pango.attr_font_features_new("tnum"))
            layout.set_attributes(attrs)
        except (AttributeError, TypeError):
            pass
    layout.set_text(value, -1)
    _ink, logical = layout.get_pixel_extents()
    w, h = logical.width, logical.height
    if align == "center":
        x -= w / 2
    elif align == "right":
        x -= w
    if valign == "center":
        y -= h / 2
    elif valign == "bottom":
        y -= h
    cr.move_to(x, y)
    if rgba is None:
        from .theme import current

        rgba = current().text
    cr.set_source_rgba(*rgba)
    PangoCairo.show_layout(cr, layout)
    return w, h
