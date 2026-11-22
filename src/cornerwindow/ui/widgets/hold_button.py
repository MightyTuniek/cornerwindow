"""A round button that must be held to confirm; a ring fills while you hold."""

from __future__ import annotations

import math
import time
from typing import Callable

from gi.repository import Gdk, Gtk

from ...core.anim import Animator, Spring
from .. import icons, theme
from ..draw import mix, platter_rim


class HoldButton(Gtk.EventBox):
    def __init__(self, icon: str, title: str, on_confirm: Callable[[], None], hold_ms: int = 600,
                 color=(1.0, 0.271, 0.227), size: int = 40) -> None:
        super().__init__()
        self.set_visible_window(False)
        self.set_above_child(True)  # the whole button (disc and label) takes the clicks
        self.icon = icon
        self.on_confirm = on_confirm
        self.hold = hold_ms / 1000.0
        self.color = (*color, 1.0)
        self.size = size
        self.set_tooltip_text(f"Hold to {title.lower()}")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.disc = Gtk.DrawingArea()
        self.disc.set_size_request(size + 6, size + 6)
        self.disc.set_halign(Gtk.Align.CENTER)
        self.disc.connect("draw", self._draw)
        box.pack_start(self.disc, False, False, 0)
        text = Gtk.Label(label=title)
        text.get_style_context().add_class("cw-tile-label")
        box.pack_start(text, False, False, 0)
        self.add(box)

        self.progress = 0.0
        self.release = Spring(0.0, response=0.3, damping_fraction=1.0)
        self.pop = Spring(0.0, response=0.3, damping_fraction=0.5)
        self.hover = Spring(0.0, response=0.2, damping_fraction=1.0)
        self.anim = Animator(self.disc, self._frame, [self.release, self.pop, self.hover])
        self._pressed_at: float | None = None

        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                        | Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.connect("button-press-event", self._press)
        self.connect("button-release-event", self._up)
        self.connect("enter-notify-event", lambda *_: self._set_hover(1.0))
        self.connect("leave-notify-event", self._leave)

    def _set_hover(self, value: float) -> None:
        self.hover.to(value)
        self.anim.kick()

    def _press(self, _w, event) -> bool:
        if event.button == 1 and event.type == Gdk.EventType.BUTTON_PRESS:
            self._pressed_at = time.monotonic()
            self.anim.kick(extra_frames=int(self.hold * 240) + 10)
        return True

    def _up(self, *_args) -> bool:
        self._cancel()
        return True

    def _leave(self, *_args) -> bool:
        self._set_hover(0.0)
        self._cancel()
        return False

    def _cancel(self) -> None:
        if self._pressed_at is None:
            return
        self._pressed_at = None
        self.release.snap(self.progress)
        self.release.to(0.0)
        self.anim.kick()

    def _frame(self) -> None:
        if self._pressed_at is not None:
            self.progress = min((time.monotonic() - self._pressed_at) / self.hold, 1.0)
            if self.progress >= 1.0:
                self._pressed_at = None
                self.release.snap(0.0)
                self.pop.snap(1.0)
                self.pop.to(0.0)
                self.progress = 0.0
                self.on_confirm()
        else:
            self.progress = self.release.value
        self.disc.queue_draw()

    def _draw(self, widget, cr) -> bool:
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        cx, cy = w / 2, h / 2
        r = self.size / 2 * (1.0 - 0.06 * self.progress + 0.08 * self.pop.value)
        p = theme.current()
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.set_source_rgba(*mix(mix(p.hold_bg, p.hold_bg_hover, self.hover.value), self.color, self.progress * 0.5))
        cr.fill()
        if p.platter_rim:
            cr.arc(cx, cy, r - 0.5, 0, 2 * math.pi)
            platter_rim(cr, p.platter_rim * 0.8, cy - r, 2 * r)
            cr.new_path()
        if self.progress > 0.001:
            cr.set_line_width(2.5)
            cr.set_line_cap(1)
            cr.set_source_rgba(*self.color)
            cr.arc(cx, cy, r - 1.5, -math.pi / 2, -math.pi / 2 + 2 * math.pi * self.progress)
            cr.stroke()
        icons.paint(cr, self.icon, cx, cy, round(self.size * 0.42), mix(p.hold_icon, (1, 1, 1, 1), self.progress), widget.get_scale_factor())
        return True
