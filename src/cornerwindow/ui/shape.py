"""The island's outline: one shape that is a quarter-circle when collapsed and a
panel with a continuous ("squircle") inner corner and concave "ears" when open.

Geometry is computed in a canonical frame where the screen corner is the origin
and x/y grow *into* the screen. `corner_transform` maps it to any real corner.

    (0,0) ────────────────────── (w+f,0)          f = ear (concave fillet) size
      │                      ╭─╯                  R = inner corner radius
      │        panel         │                    ξ = corner smoothing (0 = circular)
      │                      │
      │                     ╭╯ (w,h)  <- continuous corner, radius R
     ╭┴─────────────────────╯
    (0,h+f)

With w = h = R and f = 0 the outline degenerates to a perfect quarter-circle.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

KAPPA = 0.5522847498  # cubic bezier approximation of a quarter circle


@dataclass(frozen=True)
class ShapeParams:
    w: float
    h: float
    radius: float
    ears: float = 0.0
    smoothing: float = 0.6


Op = tuple  # ("M", x, y) | ("L", x, y) | ("C", x1, y1, x2, y2, x, y) | ("Z",)


def _smooth_corner(corner: tuple[float, float], dir_in: tuple[float, float],
                   dir_out: tuple[float, float], radius: float, budget: float,
                   smoothing: float) -> list[Op]:
    """Ops for a right-hand turn at `corner`, Figma-style corner smoothing.

    Starts with a line to the corner's entry point; ends on the outgoing edge.
    """
    cx, cy = corner

    def to_global(u: float, v: float) -> tuple[float, float]:
        return (cx + u * dir_in[0] + v * dir_out[0], cy + u * dir_in[1] + v * dir_out[1])

    if radius <= 0.01:
        return [("L", cx, cy)]
    radius = min(radius, budget)
    p = min((1.0 + smoothing) * radius, budget)
    xi = max(0.0, min(smoothing, p / radius - 1.0))

    arc_measure = 90.0 * (1.0 - xi)
    arc_section = math.sin(math.radians(arc_measure / 2.0)) * radius * math.sqrt(2.0)
    angle_alpha = (90.0 - arc_measure) / 2.0
    p3_to_p4 = radius * math.tan(math.radians(angle_alpha / 2.0))
    angle_beta = 45.0 * xi
    c = p3_to_p4 * math.cos(math.radians(angle_beta))
    d = c * math.tan(math.radians(angle_beta))
    b = max((p - arc_section - c - d) / 3.0, 0.0)
    a = 2.0 * b

    ops: list[Op] = []
    p0 = (-p, 0.0)
    ops.append(("L", *to_global(*p0)))
    p1 = (-p + a + b + c, d)
    if a + b + c > 1e-6:
        ops.append(("C", *to_global(-p + a, 0.0), *to_global(-p + a + b, 0.0), *to_global(*p1)))

    # circular middle section, centre at (-R, R) in local coordinates
    p2 = (p1[0] + arc_section, p1[1] + arc_section)
    centre = (-radius, radius)
    theta = math.radians(arc_measure)
    k = 4.0 / 3.0 * math.tan(theta / 4.0) * radius

    def tangent(pt: tuple[float, float]) -> tuple[float, float]:
        ru, rv = (pt[0] - centre[0]) / radius, (pt[1] - centre[1]) / radius
        return (-rv, ru)

    t1, t2 = tangent(p1), tangent(p2)
    ops.append(("C", *to_global(p1[0] + k * t1[0], p1[1] + k * t1[1]),
                *to_global(p2[0] - k * t2[0], p2[1] - k * t2[1]), *to_global(*p2)))

    if a + b + c > 1e-6:
        ops.append(("C", *to_global(p2[0] + d, p2[1] + c), *to_global(p2[0] + d, p2[1] + b + c),
                    *to_global(p2[0] + d, p2[1] + a + b + c)))
    return ops


def outline(params: ShapeParams) -> list[Op]:
    w, h = max(params.w, 0.0), max(params.h, 0.0)
    f = max(0.0, min(params.ears, w * 0.5, h * 0.5))
    budget = max(min(w, h) - f, 0.0)
    radius = max(0.0, min(params.radius, budget))
    ops: list[Op] = [("M", 0.0, 0.0)]

    # along the screen edge, then the concave ear into the panel's far side
    if f > 0.01:
        ops.append(("L", w + f, 0.0))
        ops.append(("C", w + f - KAPPA * f, 0.0, w, f - KAPPA * f, w, f))
    else:
        ops.append(("L", w, 0.0))

    # down the far side to the continuous inner corner
    budget_corner = max(min(w - f, h - f), 0.0) if f > 0.01 else min(w, h)
    ops.extend(_smooth_corner((w, h), (0.0, 1.0), (-1.0, 0.0), radius, budget_corner,
                              params.smoothing))

    # along the bottom to the second ear, back up the other screen edge
    if f > 0.01:
        ops.append(("L", f, h))
        ops.append(("C", f - KAPPA * f, h, 0.0, h + f - KAPPA * f, 0.0, h + f))
    else:
        ops.append(("L", 0.0, h))
    ops.append(("Z",))
    return ops


def draw(cr, ops: list[Op]) -> None:
    cr.new_path()
    for op in ops:
        kind = op[0]
        if kind == "M":
            cr.move_to(op[1], op[2])
        elif kind == "L":
            cr.line_to(op[1], op[2])
        elif kind == "C":
            cr.curve_to(*op[1:])
        else:
            cr.close_path()


def corner_transform(corner: str, width: float, height: float) -> tuple[float, float, float, float]:
    """(translate_x, translate_y, scale_x, scale_y) mapping canonical -> window coords."""
    right = corner.endswith("right")
    bottom = corner.startswith("bottom")
    return (width if right else 0.0, height if bottom else 0.0, -1.0 if right else 1.0,
            -1.0 if bottom else 1.0)


def quarter_circle_rows(radius: float, step: int = 2) -> list[tuple[int, int, int, int]]:
    """Canonical-frame rectangles approximating a quarter disc (for the input region)."""
    rows = []
    r = max(int(math.ceil(radius)), 1)
    for y in range(0, r, step):
        yc = min(y + step, r)
        width = math.sqrt(max(radius * radius - y * y, 0.0))
        rows.append((0, y, int(math.ceil(width)), yc - y))
    return rows
