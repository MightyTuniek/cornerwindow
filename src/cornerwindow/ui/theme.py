"""Colour palettes: the default black island, and Liquid Glass in dark and light.

Custom-drawn widgets read `theme.current()` at draw time instead of hard-coding
colours, so a theme switch is just a redraw. CSS follows via two classes on the
root widgets: `cw-glass` (glass theme) and `cw-light` (light appearance).

    from cornerwindow.ui import theme
    p = theme.current()
    cr.set_source_rgba(*p.text)

Module authors: use `p.text`, `p.text_dim`, `p.track`, `p.fill`, `p.ink_on_fill`,
`p.tile_off` … rather than literal whites, and your cards follow the theme.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable

RGBA = tuple[float, float, float, float]


@dataclass(frozen=True)
class GlassLook:
    """How the glass material itself is shaded (see native/glass.c)."""

    tint: tuple[float, float, float] = (0.06, 0.06, 0.07)
    tint_alpha: float = 0.3      # at tint = 0; config `glass.tint` adds up to tint_range
    tint_range: float = 0.35
    saturation: float = 1.5
    brightness: float = 0.0
    rim: float = 0.9
    rim_ambient: float = 0.5     # share of the rim lit from every angle: a continuous white border
    rim_opposite: float = 0.55
    shade: float = 0.15
    shadow: float = 1.0          # multiplier for the outer shadow


@dataclass(frozen=True)
class Palette:
    name: str
    glass: bool
    light: bool
    text: RGBA
    text_dim: RGBA
    track: RGBA
    track_hover: RGBA
    fill: RGBA
    fill_dim: RGBA
    ink_on_track: RGBA
    ink_dim: RGBA
    ink_on_fill: RGBA
    tile_off: RGBA
    tile_off_hover: RGBA
    tile_icon_off: RGBA
    tile_icon_on: RGBA
    hold_bg: RGBA
    hold_bg_hover: RGBA
    hold_icon: RGBA
    chart: tuple[float, float, float]
    platter_rim: float           # specular hairline on discs/pills (0 = none)
    look: GlassLook = field(default_factory=GlassLook)

    @property
    def css_classes(self) -> tuple[str, ...]:
        return tuple(c for c, on in (("cw-glass", self.glass), ("cw-light", self.light)) if on)


WHITE = (1.0, 1.0, 1.0, 1.0)

BLACK = Palette(
    name="black", glass=False, light=False,
    text=(0.96, 0.96, 0.97, 1.0), text_dim=(1.0, 1.0, 1.0, 0.55),
    track=(1.0, 1.0, 1.0, 0.13), track_hover=(1.0, 1.0, 1.0, 0.18),
    fill=(0.96, 0.96, 0.97, 1.0), fill_dim=(0.6, 0.6, 0.62, 1.0),
    ink_on_track=(1.0, 1.0, 1.0, 0.92), ink_dim=(1.0, 1.0, 1.0, 0.45), ink_on_fill=(0.0, 0.0, 0.0, 0.85),
    tile_off=(1.0, 1.0, 1.0, 0.13), tile_off_hover=(1.0, 1.0, 1.0, 0.2),
    tile_icon_off=WHITE, tile_icon_on=WHITE,
    hold_bg=(1.0, 1.0, 1.0, 0.12), hold_bg_hover=(1.0, 1.0, 1.0, 0.2), hold_icon=(1.0, 1.0, 1.0, 0.95),
    chart=(1.0, 1.0, 1.0), platter_rim=0.0,
)

GLASS_DARK = replace(
    BLACK, name="glass-dark", glass=True,
    track=(1.0, 1.0, 1.0, 0.14), track_hover=(1.0, 1.0, 1.0, 0.2),
    tile_off=(1.0, 1.0, 1.0, 0.14), tile_off_hover=(1.0, 1.0, 1.0, 0.22),
    hold_bg=(1.0, 1.0, 1.0, 0.14), hold_bg_hover=(1.0, 1.0, 1.0, 0.22),
    platter_rim=0.32,
    look=GlassLook(),
)

GLASS_LIGHT = Palette(
    name="glass-light", glass=True, light=True,
    text=(0.11, 0.11, 0.12, 1.0), text_dim=(0.0, 0.0, 0.0, 0.5),
    track=(0.0, 0.0, 0.0, 0.08), track_hover=(0.0, 0.0, 0.0, 0.12),
    fill=(1.0, 1.0, 1.0, 0.98), fill_dim=(0.78, 0.78, 0.8, 1.0),
    ink_on_track=(0.0, 0.0, 0.0, 0.72), ink_dim=(0.0, 0.0, 0.0, 0.35), ink_on_fill=(0.0, 0.0, 0.0, 0.82),
    tile_off=(1.0, 1.0, 1.0, 0.55), tile_off_hover=(1.0, 1.0, 1.0, 0.72),
    tile_icon_off=(0.11, 0.11, 0.12, 0.9), tile_icon_on=WHITE,
    hold_bg=(1.0, 1.0, 1.0, 0.55), hold_bg_hover=(1.0, 1.0, 1.0, 0.72), hold_icon=(0.11, 0.11, 0.12, 0.9),
    chart=(0.11, 0.11, 0.12), platter_rim=0.7,
    look=GlassLook(tint=(0.97, 0.97, 0.98), tint_alpha=0.34, tint_range=0.4, saturation=1.7,
                   brightness=0.08, rim=0.75, rim_ambient=0.55, rim_opposite=0.6, shade=0.06, shadow=0.6),
)

PALETTES = {p.name: p for p in (BLACK, GLASS_DARK, GLASS_LIGHT)}

_current: Palette = BLACK
_listeners: list[Callable[[Palette], None]] = []


def current() -> Palette:
    return _current


def set_current(palette: Palette) -> bool:
    """Switch palettes; returns True (and tells listeners) if it changed."""
    global _current
    if palette is _current:
        return False
    _current = palette
    for fn in list(_listeners):
        fn(palette)
    return True


def on_change(fn: Callable[[Palette], None]) -> None:
    _listeners.append(fn)


def resolve(glass: bool, appearance: str, system_dark: bool, luma: float | None = None,
            previous: Palette | None = None) -> Palette:
    """Which palette to use.

    appearance: "system" follows the desktop's light/dark setting, "light"/"dark"
    force one, "adaptive" picks by how bright the content behind the panel is
    (with hysteresis, so it doesn't flicker around the threshold).
    """
    if not glass:
        return BLACK
    if appearance == "light":
        return GLASS_LIGHT
    if appearance == "dark":
        return GLASS_DARK
    if appearance == "adaptive" and luma is not None:
        was_light = previous is GLASS_LIGHT
        threshold = 0.45 if was_light else 0.58
        return GLASS_LIGHT if luma > threshold else GLASS_DARK
    if appearance == "adaptive" and previous in (GLASS_LIGHT, GLASS_DARK):
        return previous
    return GLASS_DARK if system_dark else GLASS_LIGHT
