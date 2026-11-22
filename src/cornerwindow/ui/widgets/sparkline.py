"""Smooth mini history chart drawn behind a metric."""

from __future__ import annotations

from collections import deque

import cairo
from gi.repository import Gtk


class Sparkline(Gtk.DrawingArea):
    def __init__(self, points: int = 40, color=(1.0, 1.0, 1.0), maximum: float | None = 100.0,
                 height: int = 26) -> None:
        super().__init__()
        self.data: deque[float] = deque(maxlen=points)
        self.color = color
        self.maximum = maximum
        self.set_size_request(-1, height)
        self.connect("draw", self._draw)

    def push(self, value: float) -> None:
        self.data.append(max(value, 0.0))
        if self.get_mapped():
            self.queue_draw()

    def _draw(self, widget, cr) -> bool:
        if len(self.data) < 2:
            return False
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        top = self.maximum or max(max(self.data), 1e-6) * 1.15
        n = self.data.maxlen or len(self.data)
        step = w / max(n - 1, 1)
        offset = (n - len(self.data)) * step
        pts = [(offset + i * step, h - 2 - (min(v, top) / top) * (h - 4)) for i, v in enumerate(self.data)]

        cr.move_to(*pts[0])
        for i in range(1, len(pts)):  # Catmull-Rom -> Bezier for a smooth line
            p0 = pts[i - 2] if i >= 2 else pts[i - 1]
            p1, p2 = pts[i - 1], pts[i]
            p3 = pts[i + 1] if i + 1 < len(pts) else p2
            c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
            c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
            cr.curve_to(*c1, *c2, *p2)
        line = cr.copy_path()

        cr.line_to(pts[-1][0], h)
        cr.line_to(pts[0][0], h)
        cr.close_path()
        gradient = cairo.LinearGradient(0, 0, 0, h)
        gradient.add_color_stop_rgba(0, *self.color, 0.28)
        gradient.add_color_stop_rgba(1, *self.color, 0.0)
        cr.set_source(gradient)
        cr.fill()

        cr.new_path()
        cr.append_path(line)
        cr.set_source_rgba(*self.color, 0.9)
        cr.set_line_width(1.5)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.stroke()
        return True
