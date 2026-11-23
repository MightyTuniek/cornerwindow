"""The panel's content grid, with staggered entrance animation.

During the morph each card is rendered once into an offscreen surface and then
composited with its own opacity/offset/scale every frame. Nothing is laid out or
re-rendered mid-animation, so the morph stays at full frame rate. Once the
island settles open, cards are drawn live as normal GTK widgets.

Cards closest to the screen corner appear first, so content seems to pour out
of the corner.

Each column sits in a scroller that is exactly as tall as its cards until it
would pass the screen edge; only then does it scroll (wheel or touchpad, no
scrollbar: a scrollbar would take width from the cards; the end with more fades
out instead, see style.css). A column that fits behaves as if it weren't there.
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


class ColumnScroller(Gtk.ScrolledWindow):
    """A column that scrolls only when it is taller than the room it has."""

    def __init__(self, column: Gtk.Box) -> None:
        super().__init__()
        self.column = column
        # EXTERNAL: scrolls, but never shows (or reserves room for) a scrollbar
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.EXTERNAL)
        self.set_propagate_natural_height(True)
        self.set_propagate_natural_width(True)  # else GTK gives the column less than its width
        self.set_overlay_scrolling(True)
        self.set_shadow_type(Gtk.ShadowType.NONE)
        self.get_style_context().add_class("cw-column")
        self.add(column)
        viewport = self.get_child()
        if isinstance(viewport, Gtk.Viewport):
            viewport.set_shadow_type(Gtk.ShadowType.NONE)

    def natural_height(self) -> int:
        """What the cards need at the column's real width (GTK's plain height request assumes
        the narrowest width, where every label wraps, and comes out far too tall)."""
        return self.column.get_preferred_height_for_width(self.column.fixed_width)[1]

    def _height(self, width: int) -> tuple[int, int]:
        natural = self.column.get_preferred_height_for_width(max(width, 1))[1]
        room = self.get_max_content_height()
        if room > 0:
            natural = min(natural, room)
        return natural, natural

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_get_preferred_height(self):
        return self._height(self.column.fixed_width)

    def do_get_preferred_height_for_width(self, width):
        return self._height(width)

    def do_draw(self, cr) -> bool:
        # On screen the viewport's window clips the cards; drawing offscreen (snapshots, README
        # pictures) a widget with its own window (a slider) would spill past the column without this.
        cr.save()
        cr.rectangle(0, 0, self.get_allocated_width(), self.get_allocated_height())
        cr.clip()
        Gtk.ScrolledWindow.do_draw(self, cr)
        cr.restore()
        return False


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
        self.scrollers: list[ColumnScroller] = []
        self.cards: list[Gtk.Widget] = []
        self.height_limit: int | None = None  # what the grid may take (screen minus padding)
        self._top_height = -1
        self.top.connect("size-allocate", self._top_allocated)

        # animation inputs, set by the island every frame
        self.reveal = 1.0  # 0..1 how open the island is
        self.alpha = 1.0  # global content opacity
        self.live = True  # draw normally (no snapshots)
        self.clip_path = None  # callable(cr) that sets the island outline in our coords
        self.opening = True  # False while closing
        self._whole: cairo.ImageSurface | None = None
        # [card, allocation, rendered surface, order, column clip rect or None]
        self._snapshots: list[list] | None = None

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
        scroller = ColumnScroller(column)
        scroller.set_valign(Gtk.Align.END if self.corner.startswith("bottom") else Gtk.Align.START)
        self.columns_box.pack_start(scroller, False, False, 0)
        self.columns.append(column)
        self.scrollers.append(scroller)
        self._apply_limit()
        return column

    # -- fitting on the screen ---------------------------------------------------------------
    def limit_height(self, height: int) -> None:
        """The grid may be at most `height` tall: taller columns scroll."""
        self.height_limit = int(height)
        self._apply_limit()

    def column_room(self) -> int | None:
        """How tall a column may be before it scrolls (None: no limit known yet)."""
        if self.height_limit is None:
            return None
        top = self.top.get_preferred_height()[1] if self.top.get_children() else 0
        gap = self.get_spacing() if top else 0
        return max(120, self.height_limit - top - gap)

    def column_fit(self) -> list[tuple[int, int | None]]:
        """(the height a column's cards want, the room it has) for every column, left to right."""
        room = self.column_room()
        return [(s.natural_height(), room) for s in self.scrollers]

    def _apply_limit(self) -> None:
        room = self.column_room()
        for scroller in self.scrollers:
            scroller.set_max_content_height(room if room is not None else -1)

    def _top_allocated(self, _top, alloc) -> None:
        # the command bar grows when its ? list or results open: give the columns less room
        if alloc.height != self._top_height:
            self._top_height = alloc.height
            from gi.repository import GLib

            GLib.idle_add(lambda: self._apply_limit() and False)

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
            clip = self._column_clip(card)
            if clip is not None and (alloc.y + alloc.height <= clip[1] or alloc.y >= clip[1] + clip[3]):
                continue  # scrolled out of view
            self._snapshots.append([card, alloc, None, order, clip])

    def _column_clip(self, card: Gtk.Widget) -> tuple[int, int, int, int] | None:
        """The visible part of the card's column (grid coordinates), when the column scrolls."""
        parent = card.get_parent()
        while parent is not None and not isinstance(parent, ColumnScroller):
            parent = parent.get_parent()
        if parent is None:
            return None
        adjustment = parent.get_vadjustment()
        if adjustment.get_upper() - adjustment.get_page_size() <= 0.5:
            return None  # it fits: nothing to clip
        visible = parent.get_child() or parent  # the viewport: exactly what shows
        x, y = _offset(visible, self)
        return x, y, visible.get_allocated_width(), visible.get_allocated_height()

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
            card, alloc, surface, order, clip = entry
            # every card ramps over the same share of `reveal`, shifted by its order
            e = ease_out_cubic(clamp(self.reveal * span - order * self.stagger))
            alpha = e * self.alpha
            if alpha <= 0.002:
                continue
            if surface is None:
                surface = entry[2] = self._render(card.draw, alloc.width, alloc.height)
            cr.save()
            if clip is not None:
                cr.rectangle(*clip)
                cr.clip()
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
