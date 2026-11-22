"""Liquid Glass surfaces: a live backdrop seen through a lens-shaped piece of glass.

Not glassmorphism (blur + white wash + border). Liquid Glass *bends* light:

  * lensing: within `bezel` px of a visible edge the glass is a convex rim, so
    the backdrop is sampled from further inside (content bulges toward the
    edge), with a faint colour split where the bend is strongest
  * frost: from clear (no blur) to frosted (blurred, saturated)
  * a two-sided specular rim lit from `light_angle`, plus a thin inner glow
  * tint for legibility, light or dark
  * it materialises by ramping the bending up (`strength`), not by fading

The heavy lifting is per pixel, in native/glass.c. This module turns an outline
(any cairo path) into the kernel's inputs: a coverage mask, the visible edges as
segments with smooth vertex normals, the captured backdrop. It caches each one
so a settled panel costs nothing until something behind it changes.
"""

from __future__ import annotations

import ctypes
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

import cairo

from .. import native
from ..core.anim import clamp
from .theme import Palette

_pool: ThreadPoolExecutor | None = None
_THREADS = max(1, min(4, (os.cpu_count() or 2) // 2))
_DEBUG = bool(os.environ.get("CORNERWINDOW_GLASS_DEBUG"))
LO_SCALE = 4  # the frosted backdrop is blurred at a quarter of the resolution


def _executor() -> ThreadPoolExecutor:
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=_THREADS, thread_name_prefix="cw-glass")
    return _pool


@dataclass(frozen=True)
class GlassConfig:
    frost: float = 0.5        # 0 clear … 1 frosted
    tint: float = 0.5         # 0 … 1, more tint = more legible, less see-through
    refraction: float = 1.0   # lens strength multiplier
    dispersion: float = 0.3   # colour fringing at the rim
    light_angle: float = -45  # degrees, 0 = from the top, -90 = from the left

    @classmethod
    def from_section(cls, section: dict) -> "GlassConfig":
        def num(key, default, lo, hi):
            try:
                return max(lo, min(hi, float(section.get(key, default))))
            except (TypeError, ValueError):
                return default

        return cls(num("frost", 0.5, 0, 1), num("tint", 0.5, 0, 1), num("refraction", 1.0, 0, 3),
                   num("dispersion", 0.3, 0, 1), num("light_angle", -45, -360, 360))


Edge = tuple[str, float]  # ("x", 0.0): points with x == 0 lie on a screen edge


def segments(path: list, screen_edges: list[Edge], tolerance: float = 0.6) -> list[tuple]:
    """Visible edge segments of a flattened cairo path, with smooth outward normals.

    Returns (x0, y0, x1, y1, n0x, n0y, n1x, n1y) tuples. Segments that lie on a
    screen edge are dropped: the glass continues off-screen there, so it has no rim.
    Normals are averaged at shared vertices, so curves light smoothly instead of
    in facets. Orientation comes from the polygon's signed area (y down).
    """
    polygons: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    for kind, points in path:
        if kind == cairo.PATH_MOVE_TO:
            if len(current) > 2:
                polygons.append(current)
            current = [points]
        elif kind == cairo.PATH_LINE_TO:
            if not current or math.dist(current[-1], points) > 1e-3:
                current.append(points)
        elif kind == cairo.PATH_CLOSE_PATH:
            if len(current) > 2:
                polygons.append(current)
            current = []
    if len(current) > 2:
        polygons.append(current)

    def on_edge(a, b) -> bool:
        for axis, value in screen_edges:
            i = 0 if axis == "x" else 1
            if abs(a[i] - value) < tolerance and abs(b[i] - value) < tolerance:
                return True
        return False

    out = []
    for poly in polygons:
        if math.dist(poly[0], poly[-1]) < 1e-3:
            poly = poly[:-1]
        n = len(poly)
        if n < 3:
            continue
        area = sum(poly[i][0] * poly[(i + 1) % n][1] - poly[(i + 1) % n][0] * poly[i][1] for i in range(n))
        sign = 1.0 if area > 0 else -1.0
        faces = []
        for i in range(n):
            (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % n]
            length = math.hypot(x1 - x0, y1 - y0) or 1.0
            faces.append((sign * (y1 - y0) / length, -sign * (x1 - x0) / length))
        visible = [not on_edge(poly[i], poly[(i + 1) % n]) for i in range(n)]
        for i in range(n):
            if not visible[i]:
                continue
            prev, nxt = (i - 1) % n, (i + 1) % n
            f = faces[i]
            n0 = _avg(faces[prev], f) if visible[prev] else f
            n1 = _avg(f, faces[nxt]) if visible[nxt] else f
            (x0, y0), (x1, y1) = poly[i], poly[nxt]
            out.append((x0, y0, x1, y1, *n0, *n1))
    return out


def _avg(a, b):
    x, y = a[0] + b[0], a[1] + b[1]
    length = math.hypot(x, y)
    return (x / length, y / length) if length > 1e-6 else a


class GlassRenderer:
    """Renders one glass surface (the island, or the settings window).

    `track` is a backdrop handle (platform/backdrop). Call `render()` from the
    draw handler; it returns (surface, x, y) in window coordinates, or None.
    """

    def __init__(self, track) -> None:
        self.track = track
        self._shape_key = None
        self._mask: cairo.ImageSurface | None = None
        self._field = None  # (dist, nx, ny) ctypes float arrays
        self._field_capacity = 0
        self._lo_origin = (0, 0)
        self._segs = None
        self._lo_key = None
        self._lo = None
        self._lo_dims = (0, 0, 0)
        self._lo_tinted = None
        self._out_key = None
        self._out: cairo.ImageSurface | None = None
        self.last_luma: float | None = None

    def close(self) -> None:
        if self.track is not None:
            self.track.untrack()
            self.track = None

    def invalidate(self) -> None:
        self._out_key = None

    # -- inputs ---------------------------------------------------------------------------
    def _prepare_shape(self, key, path_fn: Callable, bbox, scale: float, screen_edges: list[Edge],
                       bezel: float) -> None:
        if key == self._shape_key:
            return
        bx, by, bw, bh = bbox
        w, h = max(int(math.ceil(bw * scale)), 1), max(int(math.ceil(bh * scale)), 1)
        mask = cairo.ImageSurface(cairo.FORMAT_A8, w, h)
        cr = cairo.Context(mask)
        cr.scale(scale, scale)
        cr.translate(-bx, -by)
        cr.set_tolerance(0.05)
        path_fn(cr)
        flat = cr.copy_path_flat()
        cr.fill()
        mask.flush()
        # segments in output (device) pixels
        edges = [(axis, (value - (bx if axis == "x" else by)) * scale) for axis, value in screen_edges]
        scaled = [(kind, ((pts[0] - bx) * scale, (pts[1] - by) * scale) if pts else pts)
                  for kind, pts in ((k, p) for k, p in flat)]
        segs = segments(scaled, edges)
        if w * h > self._field_capacity:  # grow only: the morph changes size every frame
            self._field_capacity = int(w * h * 1.3)
            self._field = tuple((ctypes.c_float * self._field_capacity)() for _ in range(3))
        lib = native.load()
        array = (native.Seg * max(len(segs), 1))(*[native.Seg(*s) for s in segs])
        dist, nx, ny = self._field
        lib.cw_edge_field(array, len(segs), w, h, bezel, dist, nx, ny)
        self._mask = mask
        self._shape_key = key
        self._out_key = None

    def _prepare_lo(self, capture, frost: float, need=None) -> None:
        """Blur the backdrop at quarter resolution. `need` (x, y, w, h in capture pixels)
        limits it to what the shape can sample, e.g. just the droplet while collapsed."""
        base, cw, ch, stride, _x, _y, generation = capture
        radius = max(int(round(frost * 16 / LO_SCALE)), 0)
        region = (0, 0, cw, ch)
        if need is not None and need[2] * need[3] < 0.25 * cw * ch:
            margin = (3 * radius + 2) * LO_SCALE
            x0 = max(0, (int(need[0]) - margin) // LO_SCALE * LO_SCALE)
            y0 = max(0, (int(need[1]) - margin) // LO_SCALE * LO_SCALE)
            x1 = min(cw, int(math.ceil(need[0] + need[2])) + margin)
            y1 = min(ch, int(math.ceil(need[1] + need[3])) + margin)
            if x1 > x0 and y1 > y0:
                region = (x0, y0, x1 - x0, y1 - y0)
        old = self._lo_key
        if (old is not None and old[0] == (generation, cw, ch, stride, radius, base)
                and old[1][0] <= region[0] and old[1][1] <= region[1]
                and old[1][0] + old[1][2] >= region[0] + region[2]
                and old[1][1] + old[1][3] >= region[1] + region[3]):
            return  # the blur we have still covers it
        rx, ry, rw, rh = region
        lw, lh = (rw + LO_SCALE - 1) // LO_SCALE, (rh + LO_SCALE - 1) // LO_SCALE
        if self._lo is None or len(self._lo) < lw * lh:
            self._lo = (ctypes.c_uint32 * int(lw * lh * 1.3 + 16))()
        self._lo_dims = (lw, lh, lw * 4)
        self._lo_origin = (rx, ry)
        lib = native.load()
        lib.cw_prepare_backdrop(base + ry * stride + rx * 4, rw, rh, stride, LO_SCALE, radius,
                                self._lo, lw, lh, lw * 4)
        self.last_luma = lib.cw_luma(self._lo, lw, lh, lw * 4)
        self._lo_key = ((generation, cw, ch, stride, radius, base), region)
        self._out_key = None

    def prefetch(self, rect, frost: float) -> None:
        """Capture and blur `rect` ahead of time (e.g. when the pointer reaches the trigger),
        so the first frame of opening doesn't pay for it."""
        if self.track is None or native.load() is None:
            return
        capture = self.track.capture(rect)
        if capture is not None:
            self._prepare_lo(capture, clamp(frost))

    def _paint_interior(self, lib, p, out: cairo.ImageSurface, res: float) -> None:
        """Frosted glass's flat middle is the tinted, blurred backdrop scaled up: cairo's
        bilinear scaler does that several times faster than per-pixel C, so the kernel
        only shades the rim."""
        lw, lh, lstride = self._lo_dims
        if (self._lo_tinted is None or (self._lo_tinted.get_width(), self._lo_tinted.get_height()) != (lw, lh)
                or self._lo_tinted.get_stride() != lstride):
            self._lo_tinted = cairo.ImageSurface(cairo.FORMAT_RGB24, lw, lh)
            if self._lo_tinted.get_stride() != lstride:
                self._lo_tinted = None
                return
        tinted = self._lo_tinted
        tinted.flush()
        data = tinted.get_data()
        lib.cw_tint_lo(ctypes.byref(p), ctypes.addressof((ctypes.c_char * len(data)).from_buffer(data)))
        tinted.mark_dirty()
        cr = cairo.Context(out)
        cr.scale(1.0 / res, 1.0 / res)  # output pixels
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.scale(1.0 / p.src_step, 1.0 / p.src_step)  # backdrop pixels
        cr.translate(p.lo_x - p.src_ox, p.lo_y - p.src_oy)
        cr.scale(p.lo_scale, p.lo_scale)
        cr.set_source_surface(tinted, 0, 0)
        pattern = cr.get_source()
        pattern.set_filter(cairo.FILTER_BILINEAR)
        pattern.set_extend(cairo.EXTEND_PAD)
        cr.paint()
        del cr
        out.flush()
        p.skip_interior = 1

    # -- render ---------------------------------------------------------------------------
    def render(self, *, path_fn: Callable, shape_key, bbox, origin: tuple[int, int], scale: int,
               screen_edges: list[Edge], palette: Palette, config: GlassConfig, bezel: float,
               strength: float = 1.0, glow: float = 0.0, press: tuple[float, float, float] | None = None,
               capture_rect=None, refresh: bool = True, local_blur: bool = True,
               quality: float = 1.0):
        """bbox/press in window coordinates; origin = window position on the root, device px.

        refresh=False reuses the last backdrop even if something behind changed (used
        while the panel morphs: nobody sees a 0.4 s old backdrop mid-spring).
        quality < 1 renders at that fraction of the resolution (while animating)."""
        lib = native.load()
        if lib is None or self.track is None:
            return None
        bx, by, bw, bh = (int(math.floor(bbox[0])), int(math.floor(bbox[1])),
                          int(math.ceil(bbox[2])) + 1, int(math.ceil(bbox[3])) + 1)
        if bw <= 1 or bh <= 1:
            return None
        bbox = (bx, by, bw, bh)
        res = scale * quality  # output pixels per window unit
        w, h = max(int(math.ceil(bw * res)), 1), max(int(math.ceil(bh * res)), 1)
        bezel_px = max(bezel * res, 1.0)

        t0 = time.perf_counter()
        ox, oy = origin
        want = capture_rect or (ox + bx * scale, oy + by * scale, bw * scale, bh * scale)
        capture = self.track.capture(want, refresh)
        if capture is None:
            return None
        t1 = time.perf_counter()
        look = palette.look
        frost = clamp(config.frost)
        self._prepare_shape((shape_key, bbox, res, bezel_px), path_fn, bbox, res, screen_edges, bezel_px)
        t2 = time.perf_counter()
        base, cw, ch, stride, cx, cy, _gen = capture
        src_ox, src_oy = ox + bx * scale - cx, oy + by * scale - cy
        self._prepare_lo(capture, frost, (src_ox, src_oy, bw * scale, bh * scale) if local_blur else None)
        t3 = time.perf_counter()

        press_key = None if press is None or press[2] < 0.003 else tuple(round(v, 3) for v in press)
        key = (self._shape_key, self._lo_key, palette.name, config, round(strength, 3), round(glow, 3), press_key, res)
        if key == self._out_key and self._out is not None:
            return self._out, bx, by
        base, cw, ch, stride, cx, cy, _gen = capture
        if self._out is None or (self._out.get_width(), self._out.get_height()) != (w, h):
            self._out = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        self._out.set_device_scale(res, res)
        out = self._out
        out.flush()
        data = out.get_data()
        out_addr = ctypes.addressof((ctypes.c_char * len(data)).from_buffer(data))
        mask_data = self._mask.get_data()
        mask_addr = ctypes.addressof((ctypes.c_char * len(mask_data)).from_buffer(mask_data))

        angle = math.radians(config.light_angle)
        tint_a = clamp(look.tint_alpha + look.tint_range * config.tint)
        lw, lh, lstride = self._lo_dims
        p = native.Params()
        p.w, p.h, p.out_stride = w, h, out.get_stride()
        p.src, p.src_w, p.src_h, p.src_stride = base, cw, ch, stride
        p.src_ox, p.src_oy = float(ox + bx * scale - cx), float(oy + by * scale - cy)
        p.src_step = scale / res
        p.lo, p.lo_w, p.lo_h, p.lo_stride, p.lo_scale = ctypes.addressof(self._lo), lw, lh, lstride, float(LO_SCALE)
        p.lo_x, p.lo_y = map(float, self._lo_origin)
        p.mask, p.mask_stride = mask_addr, self._mask.get_stride()
        dist, nx, ny = self._field
        p.dist, p.nx, p.ny = ctypes.addressof(dist), ctypes.addressof(nx), ctypes.addressof(ny)
        p.bezel = bezel_px
        p.disp = bezel_px * 0.85 * config.refraction * strength
        p.dispersion = config.dispersion
        p.frost = clamp(frost * 3.0)  # blurred backdrop takes over quickly; frost sets the radius
        p.saturation = 1.0 + (look.saturation - 1.0) * (0.35 + 0.65 * frost)
        p.brightness = look.brightness
        p.tint_r, p.tint_g, p.tint_b = look.tint
        p.tint_a = tint_a
        p.light_x, p.light_y = math.sin(angle), -math.cos(angle)
        p.rim = look.rim * (0.35 + 0.65 * strength) * (1.0 + 0.6 * glow)
        p.rim_ambient, p.rim_opposite = look.rim_ambient, look.rim_opposite
        p.rim_glow = 3.5 * res
        p.shade = look.shade * strength
        if press_key is not None:
            p.press_x, p.press_y = (press[0] - bx) * res, (press[1] - by) * res
            p.press_r = 90.0 * res
            p.press_a = 0.22 * press[2]

        if p.frost >= 0.999 and press_key is None:
            self._paint_interior(lib, p, out, res)

        if _THREADS > 1 and h >= 96:
            ref = ctypes.byref(p)
            jobs = [_executor().submit(lib.cw_shade_rows, ref, out_addr, t, h, _THREADS)
                    for t in range(_THREADS)]
            for job in jobs:
                job.result()
        else:
            lib.cw_shade(ctypes.byref(p), out_addr)
        out.mark_dirty()
        if _DEBUG:
            t4 = time.perf_counter()
            print(f"[glass] {w}x{h} capture {1e3 * (t1 - t0):.1f} shape {1e3 * (t2 - t1):.1f} "
                  f"blur {1e3 * (t3 - t2):.1f} shade {1e3 * (t4 - t3):.1f} ms", flush=True)
        self._keep = (data, mask_data)
        self._out_key = key
        return out, bx, by
