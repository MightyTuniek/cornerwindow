"""Round Control Center toggle with a label underneath."""

from __future__ import annotations

from typing import Callable

from gi.repository import Gdk, Gtk, Pango

from ...core.anim import Animator, Spring
from .. import icons, theme
from ..draw import mix, platter_rim


class ToggleTile(Gtk.EventBox):
    def __init__(self, icon: str, title: str, on_toggle: Callable[[bool], None],
                 color=(0.039, 0.518, 1.0), size: int = 46, icon_off: str | None = None,
                 on_more: Callable[[], None] | None = None) -> None:
        super().__init__()
        self.set_visible_window(False)
        self.set_above_child(True)  # the whole tile (disc and label) takes the clicks
        self.icon = icon
        self.icon_off = icon_off or icon
        self.color = (*color, 1.0) if len(color) == 3 else color
        self.on_toggle = on_toggle
        self.size = size
        self.active = False
        self.subtitle_text = ""
        self.title = title
        self.on_more = on_more  # opens a picker: click the label/chevron, or right-click

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.disc = Gtk.DrawingArea()
        self.disc.set_size_request(size + 6, size + 6)
        self.disc.set_halign(Gtk.Align.CENTER)
        self.disc.connect("draw", self._draw)
        box.pack_start(self.disc, False, False, 0)
        self.label = Gtk.Label(label=title)
        self.label.get_style_context().add_class("cw-tile-label")
        self.label.set_ellipsize(Pango.EllipsizeMode.END)
        self.label.set_max_width_chars(9 if on_more is None else 8)
        line = Gtk.Box(spacing=1)
        line.set_halign(Gtk.Align.CENTER)
        line.pack_start(self.label, False, False, 0)
        if on_more is not None:
            from .card import resolve_icon

            chevron = Gtk.Image.new_from_icon_name(resolve_icon("chevron"), Gtk.IconSize.MENU)
            chevron.set_pixel_size(10)
            chevron.get_style_context().add_class("cw-tile-label")
            line.pack_start(chevron, False, False, 0)
        box.pack_start(line, False, False, 0)
        self.add(box)

        self.on = Spring(0.0, response=0.3, damping_fraction=1.0)
        self.press = Spring(0.0, response=0.22, damping_fraction=0.7)
        self.hover = Spring(0.0, response=0.2, damping_fraction=1.0)
        self.anim = Animator(self.disc, self.disc.queue_draw, [self.on, self.press, self.hover])

        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                        | Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.connect("button-press-event", self._pressed)
        self.connect("button-release-event", self._released)
        self.connect("enter-notify-event", lambda *_: self._set_hover(True))
        self.connect("leave-notify-event", lambda *_: self._set_hover(False))

    def set_active(self, active: bool, animate: bool = True) -> None:
        self.active = bool(active)
        if animate and self.get_mapped():
            self.on.to(1.0 if active else 0.0)
            self.anim.kick()
        else:
            self.on.snap(1.0 if active else 0.0)
            self.disc.queue_draw()

    def set_title(self, title: str) -> None:
        self.title = title
        self.label.set_text(self.subtitle_text or title)

    def set_subtitle(self, text: str | None) -> None:
        """Show e.g. the network name in place of the title (same size: nothing relayouts)."""
        self.subtitle_text = text or ""
        self.label.set_text(self.subtitle_text or self.title)
        self.set_tooltip_text(f"{self.title}: {self.subtitle_text}" if self.subtitle_text else self.title)

    def _set_hover(self, inside: bool) -> None:
        self.hover.to(1.0 if inside else 0.0)
        self.anim.kick()

    def _pressed(self, _w, event) -> bool:
        if event.button == 1 and event.type == Gdk.EventType.BUTTON_PRESS:
            self.press.to(1.0)
            self.anim.kick()
        return True

    def _released(self, _w, event) -> bool:
        if event.button == 3 and self.on_more is not None:
            self.on_more()
            return True
        if event.button != 1:
            return False
        self.press.to(0.0)
        self.anim.kick()
        alloc = self.get_allocation()
        if 0 <= event.x <= alloc.width and 0 <= event.y <= alloc.height:
            below_disc = event.y > self.disc.get_allocated_height()
            if below_disc and self.on_more is not None:
                self.on_more()  # the label opens the picker, the disc toggles
                return True
            self.set_active(not self.active)
            self.on_toggle(self.active)
        return True

    def _draw(self, widget, cr) -> bool:
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        scale = 1.0 - 0.07 * self.press.value
        r = self.size / 2 * scale
        cx, cy = w / 2, h / 2
        p = theme.current()
        off = mix(p.tile_off, p.tile_off_hover, self.hover.value)
        cr.arc(cx, cy, r, 0, 6.283185307)
        cr.set_source_rgba(*mix(off, self.color, self.on.value))
        cr.fill_preserve()
        if p.platter_rim:
            cr.new_path()
            cr.arc(cx, cy, r - 0.5, 0, 6.283185307)
            platter_rim(cr, p.platter_rim * (0.7 + 0.3 * self.on.value), cy - r, 2 * r)
        cr.new_path()
        icon = self.icon if self.active or self.on.value > 0.5 else self.icon_off
        ink = mix(p.tile_icon_off, p.tile_icon_on, self.on.value)
        icons.paint(cr, icon, cx, cy, round(self.size * 0.42), ink, widget.get_scale_factor())
        return True
