"""Control Center style slider: a thick pill that fills from the left.

* Relative dragging (grab anywhere, the value moves with your finger, no jump),
  a click without dragging jumps to that spot with a spring.
* Rubber-banding past either end, springing back on release.
* Scroll/touchpad adjust with smooth deltas.
* The icon and value are drawn twice: white on the track, black where the fill
  covers them, exactly like iOS.
"""

from __future__ import annotations

from typing import Callable

from gi.repository import Gdk, Gtk

from ...core.anim import Animator, Spring, clamp, rubber_band
from .. import icons
from .. import theme
from ..draw import mix, pill, platter_rim, text

INSET = 8.0  # room for rubber-band stretch inside the allocation


class PillSlider(Gtk.DrawingArea):
    def __init__(self, icon: str, on_change: Callable[[float, bool], None], value: float = 0.5,
                 height: int = 40, formatter: Callable[[float], str] | None = None,
                 step: float = 0.05, sensitive_icon: bool = True) -> None:
        super().__init__()
        self.icon = icon
        self.on_change = on_change
        self.formatter = formatter or (lambda v: f"{round(v * 100)}")
        self.step = step
        self.sensitive_icon = sensitive_icon
        self.value = clamp(value)
        self.dim = False  # e.g. muted
        self.set_size_request(-1, height + 4)
        self.set_hexpand(True)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                        | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.SCROLL_MASK
                        | Gdk.EventMask.SMOOTH_SCROLL_MASK | Gdk.EventMask.ENTER_NOTIFY_MASK
                        | Gdk.EventMask.LEAVE_NOTIFY_MASK)

        self.shown = Spring(self.value, response=0.28, damping_fraction=0.9, precision=0.0005)
        self.stretch = Spring(0.0, response=0.45, damping_fraction=0.55, precision=0.05)
        self.press = Spring(0.0, response=0.25, damping_fraction=0.85)
        self.hover = Spring(0.0, response=0.2, damping_fraction=1.0)
        self.anim = Animator(self, self.queue_draw, [self.shown, self.stretch, self.press, self.hover])

        self._drag = None  # (x0, value0, moved)
        self._scroll_accum = 0.0
        self.connect("draw", self._draw)
        self.connect("button-press-event", self._press)
        self.connect("button-release-event", self._release)
        self.connect("motion-notify-event", self._motion)
        self.connect("scroll-event", self._scroll)
        self.connect("enter-notify-event", lambda *_: self._hover(True))
        self.connect("leave-notify-event", lambda *_: self._hover(False))

    # -- public ---------------------------------------------------------------------------
    def set_value(self, value: float, animate: bool = True) -> None:
        if self._drag is not None:
            return  # never fight the user's finger
        self.value = clamp(value)
        if animate and self.get_mapped():
            self.shown.to(self.value)
            self.anim.kick()
        else:
            self.shown.snap(self.value)
            self.queue_draw()

    def set_dim(self, dim: bool) -> None:
        if dim != self.dim:
            self.dim = dim
            self.queue_draw()

    # -- input ----------------------------------------------------------------------------
    def _track_width(self) -> float:
        return max(self.get_allocated_width() - 2 * INSET, 1.0)

    def _hover(self, inside: bool) -> None:
        self.hover.to(1.0 if inside else 0.0)
        self.anim.kick()

    def _press(self, _w, event) -> bool:
        if event.button != 1 or event.type != Gdk.EventType.BUTTON_PRESS:
            return False
        self._drag = (event.x, self.value, False)
        self.press.to(1.0)
        self.anim.kick()
        return True

    def _motion(self, _w, event) -> bool:
        if self._drag is None:
            return False
        x0, v0, moved = self._drag
        dx = event.x - x0
        if not moved and abs(dx) < 3:
            return True
        self._drag = (x0, v0, True)
        width = self._track_width()
        raw = v0 + dx / width
        value = clamp(raw)
        overshoot_px = (raw - value) * width
        self.stretch.snap(rubber_band(overshoot_px, width * 0.12))
        if value != self.value:
            self.value = value
            self.on_change(value, False)
        self.shown.snap(value)
        self.queue_draw()
        return True

    def _release(self, _w, event) -> bool:
        if self._drag is None:
            return False
        _x0, _v0, moved = self._drag
        self._drag = None
        if not moved:
            value = clamp((event.x - INSET) / self._track_width())
            self.value = value
            self.shown.to(value, 0.32, 0.86)
        self.on_change(self.value, True)
        self.stretch.to(0.0)
        self.press.to(0.0)
        self.anim.kick()
        return True

    def _scroll(self, _w, event) -> bool:
        delta = 0.0
        if event.direction == Gdk.ScrollDirection.SMOOTH:
            ok, dx, dy = event.get_scroll_deltas()
            delta = -dy if abs(dy) >= abs(dx) else dx
            self._scroll_accum += delta
            steps = int(self._scroll_accum)
            if steps == 0:
                return True
            self._scroll_accum -= steps
            delta = steps
        elif event.direction in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.RIGHT):
            delta = 1
        elif event.direction in (Gdk.ScrollDirection.DOWN, Gdk.ScrollDirection.LEFT):
            delta = -1
        raw = self.value + delta * self.step
        value = clamp(raw)
        if raw != value:  # bump against the end
            self.stretch.kick(14.0 if raw > 1 else -14.0)
        if value != self.value:
            self.value = value
            self.on_change(value, True)
        self.shown.to(value, 0.22, 0.95)
        self.anim.kick()
        return True

    # -- drawing --------------------------------------------------------------------------
    def _draw(self, _w, cr) -> bool:
        width = self.get_allocated_width()
        height = self.get_allocated_height()
        stretch = self.stretch.value
        grow = self.press.value * 2.0
        h = height - 4 + grow - abs(stretch) * 0.12
        x = INSET - max(-stretch, 0.0) - grow / 2
        w = width - 2 * INSET + abs(stretch) + grow
        y = (height - h) / 2

        p = theme.current()
        cr.save()  # the pill clip below is undone before the rim, keeping any clip from outside
        pill(cr, x, y, w, h)
        cr.clip_preserve()
        cr.set_source_rgba(*mix(p.track, p.track_hover, self.hover.value))
        cr.fill()

        fill_w = clamp(self.shown.value) * (w - h) + h if self.shown.value > 0.001 else 0.0
        # Only cover the stretched pill when rubber-banding past the top end. The stretch
        # spring is bouncy, so springing back from the bottom end briefly overshoots
        # positive and must not paint a full fill.
        if stretch > 0 and self.shown.value >= 0.999:
            fill_w = w
        ink_size = min(h * 0.5, 20)
        icon_x = x + h / 2 + 2
        label = self.formatter(self.value)

        def ink(rgba) -> None:
            icons.paint(cr, self.icon, icon_x, y + h / 2, ink_size, rgba,
                        self.get_scale_factor(), amount=self.shown.value)
            text(cr, self, label, x + w - h / 2 + 4, y + h / 2, size=12, weight=600,
                 rgba=(rgba[0], rgba[1], rgba[2], rgba[3] * 0.85), align="right")

        on_track = p.ink_on_track if not self.dim else p.ink_dim
        ink(on_track)
        if fill_w > 0:
            cr.save()
            fill_rgba = p.fill if not self.dim else p.fill_dim
            cr.rectangle(x, y, fill_w, h)
            cr.clip()
            cr.set_source_rgba(*fill_rgba)
            cr.paint()
            ink(p.ink_on_fill)
            cr.restore()
        cr.restore()  # not reset_clip(): that would also drop the scrolling column's clip
        if p.platter_rim:
            pill(cr, x + 0.5, y + 0.5, w - 1, h - 1)
            platter_rim(cr, p.platter_rim * 0.8, y, h)
            cr.new_path()
        return True
