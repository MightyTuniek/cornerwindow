"""The panel's content grid, with staggered entrance animation.

During the morph each card is rendered once into an offscreen surface and then
composited with its own opacity/offset/scale every frame. Nothing is laid out or
re-rendered mid-animation, so the morph stays at full frame rate. Once the
island settles open, cards are drawn live as normal GTK widgets.

Cards closest to the screen corner appear first, so content seems to pour out
of the corner.
"""

from __future__ import annotations

import math

import cairo
from gi.repository import Gtk

from ..core.anim import clamp, ease_out_cubic


def _offset(widget: Gtk.Widget, ancestor: Gtk.Widget) -> tuple[int, int]:
    result = widget.translate_coordinates(ancestor, 0, 0)
    if not result:
        return 0, 0
    return tuple(result[-2:])  # PyGObject returns (x, y) or (ok, x, y) depending on version


class FixedWidth(Gtk.Box):
    """A vertical box that always asks for exactly `width` (labels wrap/ellipsize inside)."""

    def __init__(self, width: int, **kwargs) -> None:
        super().__init__(**kwargs)
        self.fixed_width = width

    def do_get_preferred_width(self):
        return self.fixed_width, self.fixed_width

    def do_get_preferred_width_for_height(self, _height):
        return self.fixed_width, self.fixed_width


class CardGrid(Gtk.Box):
    def __init__(self, corner: str, column_width: int, spacing: int = 10, stagger: float = 0.06) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
        self.corner = corner
        self.stagger = stagger
        self.column_width = column_width
        self.top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)  # command bar lives here
        self.columns_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing)
        self.columns_box.set_homogeneous(False)
        self.pack_start(self.top, False, False, 0)
        self.pack_start(self.columns_box, False, False, 0)
        self.columns: list[Gtk.Box] = []
        self.cards: list[Gtk.Widget] = []

        # animation inputs, set by the island every frame
        self.reveal = 1.0  # 0..1 how open the island is
        self.alpha = 1.0  # global content opacity
        self.live = True  # draw normally (no snapshots)
        self.clip_path = None  # callable(cr) that sets the island outline in our coords
        self.opening = True  # False while closing
        self._whole: cairo.ImageSurface | None = None
        self._snapshots: list[tuple[Gtk.Widget, Gtk.Allocation, cairo.ImageSurface, int]] | None = None

    def total_width(self) -> int:
        count = max(len(self.columns), 1)
        return count * self.column_width + (count - 1) * self.get_spacing()

    def do_get_preferred_width(self):
        width = self.total_width()
        return width, width

    def do_get_preferred_width_for_height(self, _height):
        width = self.total_width()
        return width, width

    def add_column(self) -> Gtk.Box:
        column = FixedWidth(self.column_width, orientation=Gtk.Orientation.VERTICAL,
                            spacing=self.get_spacing())
        column.set_valign(Gtk.Align.END if self.corner.startswith("bottom") else Gtk.Align.START)
        self.columns_box.pack_start(column, False, False, 0)
        self.columns.append(column)
        return column

    def add_card(self, column: Gtk.Box, widget: Gtk.Widget) -> None:
        column.pack_start(widget, False, False, 0)
        self.cards.append(widget)

    def invalidate(self) -> None:
        self._snapshots = None

    # -- drawing ----------------------------------------------------------------------------
    def _card_order(self) -> list[Gtk.Widget]:
        """Cards sorted by distance from the screen corner."""
        own = self.get_allocation()
        right = self.corner.endswith("right")
        bottom = self.corner.startswith("bottom")
        corner_x = own.width if right else 0
        corner_y = own.height if bottom else 0

        def distance(card: Gtk.Widget) -> float:
            x, y = _offset(card, self)
            alloc = card.get_allocation()
            cx, cy = x + alloc.width / 2, y + alloc.height / 2
            return math.hypot(cx - corner_x, (cy - corner_y) * 1.6)

        widgets = [self.top] + [c for c in self.cards if c.get_visible() and c.get_child_visible()]
        return sorted(widgets, key=distance)

    def _prepare(self) -> None:
        """Work out card order now; render each card lazily the first frame it shows,
        so the offscreen rendering cost is spread across the stagger."""
        self._snapshots = []
        for order, card in enumerate(self._card_order()):
            alloc = card.get_allocation()
            if alloc.width <= 1 or alloc.height <= 1 or not card.get_mapped():
                continue
            alloc.x, alloc.y = _offset(card, self)
            self._snapshots.append([card, alloc, None, order])

    def _render(self, draw, width: int, height: int) -> cairo.ImageSurface:
        scale = self.get_scale_factor()
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(width * scale, 1), max(height * scale, 1))
        surface.set_device_scale(scale, scale)
        draw(cairo.Context(surface))
        return surface

    def do_draw(self, cr) -> bool:
        if self.alpha <= 0.002 or self.reveal <= 0.002:
            self._whole = None
            return False
        if self.clip_path is not None:
            self.clip_path(cr)
            cr.clip()
        if self.live:
            self._snapshots = None
            self._whole = None
            if self.alpha < 0.999:
                cr.push_group()
                Gtk.Box.do_draw(self, cr)
                cr.pop_group_to_source()
                cr.paint_with_alpha(self.alpha)
            else:
                Gtk.Box.do_draw(self, cr)
            return False

        if not self.opening:
            # Closing: one snapshot of everything, fading and shrinking toward the corner.
            own = self.get_allocation()
            if self._whole is None:
                self._whole = self._render(lambda c: Gtk.Box.do_draw(self, c), own.width, own.height)
            scale = 0.95 + 0.05 * self.alpha
            ax = own.width if self.corner.endswith("right") else 0
            ay = own.height if self.corner.startswith("bottom") else 0
            cr.translate(ax, ay)
            cr.scale(scale, scale)
            cr.translate(-ax, -ay)
            cr.set_source_surface(self._whole, 0, 0)
            cr.paint_with_alpha(self.alpha)
            return False

        self._whole = None
        if self._snapshots is None:
            self._prepare()
        # Cards slide in from the corner's side: down from the top edge, up from the bottom.
        slide = 18.0 if self.corner.startswith("bottom") else -18.0
        count = max(len(self._snapshots), 1)
        span = 1.0 + self.stagger * (count - 1)
        for entry in self._snapshots:
            card, alloc, surface, order = entry
            # every card ramps over the same share of `reveal`, shifted by its order
            e = ease_out_cubic(clamp(self.reveal * span - order * self.stagger))
            alpha = e * self.alpha
            if alpha <= 0.002:
                continue
            if surface is None:
                surface = entry[2] = self._render(card.draw, alloc.width, alloc.height)
            cr.save()
            scale = 0.94 + 0.06 * e
            ax = alloc.x + (alloc.width if self.corner.endswith("right") else 0)
            ay = alloc.y + (alloc.height if self.corner.startswith("bottom") else 0)
            cr.translate(ax, ay + (1.0 - e) * slide)
            cr.scale(scale, scale)
            cr.translate(-ax, -ay)
            cr.set_source_surface(surface, alloc.x, alloc.y)
            cr.paint_with_alpha(alpha)
            cr.restore()
        return False
