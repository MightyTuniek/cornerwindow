"""Liquid Glass: palette choice, wallpaper placement, edge geometry and the C kernel."""

from __future__ import annotations

import ctypes
import math
import shutil

import cairo
import pytest

from cornerwindow.platform.backdrop.wallpaper import parse_color, placement, uri_to_path
from cornerwindow.platform.backdrop.x11 import bounds, contains, intersect
from cornerwindow.ui import shape, theme
from cornerwindow.ui.glass import GlassConfig, segments


# ── palette ──────────────────────────────────────────────────────────────────
def test_black_unless_glass_is_on():
    assert theme.resolve(False, "dark", True) is theme.BLACK
    assert theme.resolve(False, "light", False) is theme.BLACK


def test_system_appearance_follows_dark_mode():
    assert theme.resolve(True, "system", True) is theme.GLASS_DARK
    assert theme.resolve(True, "system", False) is theme.GLASS_LIGHT
    assert theme.resolve(True, "light", True) is theme.GLASS_LIGHT
    assert theme.resolve(True, "dark", False) is theme.GLASS_DARK


def test_adaptive_appearance_has_hysteresis():
    dark, light = theme.GLASS_DARK, theme.GLASS_LIGHT
    assert theme.resolve(True, "adaptive", True, luma=0.7, previous=dark) is light
    assert theme.resolve(True, "adaptive", True, luma=0.5, previous=dark) is dark   # below 0.58
    assert theme.resolve(True, "adaptive", True, luma=0.5, previous=light) is light  # above 0.45
    assert theme.resolve(True, "adaptive", True, luma=0.3, previous=light) is dark
    assert theme.resolve(True, "adaptive", False, luma=None, previous=dark) is dark


def test_css_classes():
    assert theme.BLACK.css_classes == ()
    assert theme.GLASS_DARK.css_classes == ("cw-glass",)
    assert theme.GLASS_LIGHT.css_classes == ("cw-glass", "cw-light")


def test_glass_config_clamps():
    cfg = GlassConfig.from_section({"frost": 3, "tint": -1, "refraction": "x"})
    assert (cfg.frost, cfg.tint, cfg.refraction) == (1.0, 0.0, 1.0)


# ── wallpaper ────────────────────────────────────────────────────────────────
def test_wallpaper_placement():
    # zoom covers the area, cropping the long side
    sx, sy, ox, oy = placement(1000, 500, 1920, 1080, "zoom")
    assert sx == sy == pytest.approx(2.16) and oy == pytest.approx(0) and ox < 0
    # scaled fits inside, letterboxed
    sx, sy, ox, oy = placement(1000, 500, 1920, 1080, "scaled")
    assert sx == pytest.approx(1.92) and ox == pytest.approx(0) and oy > 0
    # centered keeps the size
    assert placement(100, 100, 300, 200, "centered") == (1.0, 1.0, 100.0, 50.0)
    sx, sy, _ox, _oy = placement(100, 50, 300, 200, "stretched")
    assert (sx, sy) == (3.0, 4.0)


def test_wallpaper_helpers():
    assert parse_color("#ff8000") == (1.0, 128 / 255, 0.0)
    assert parse_color("nonsense", (0.1, 0.2, 0.3)) == (0.1, 0.2, 0.3)
    assert str(uri_to_path("file:///home/a%20b/x.png")) == "/home/a b/x.png"
    assert uri_to_path("resource:///x") is None


def test_rect_helpers():
    assert intersect((0, 0, 10, 10), (5, 5, 10, 10)) == (5, 5, 5, 5)
    assert intersect((0, 0, 10, 10), (10, 0, 5, 5)) is None
    assert contains((0, 0, 10, 10), (2, 2, 3, 3)) and not contains((0, 0, 10, 10), (8, 8, 3, 3))
    assert bounds((0, 0, 2, 2), (5, 5, 1, 1)) == (0, 0, 6, 6)
    assert bounds(None, (1, 1, 1, 1)) == (1, 1, 1, 1)


# ── edges ────────────────────────────────────────────────────────────────────
def _flat(ops, corner="top-right", win=(400, 600)):
    surface = cairo.ImageSurface(cairo.FORMAT_A8, 1, 1)
    cr = cairo.Context(surface)
    tx, ty, sx, sy = shape.corner_transform(corner, *win)
    cr.translate(tx, ty)
    cr.scale(sx, sy)
    shape.draw(cr, ops)
    cr.identity_matrix()
    return cr.copy_path_flat()


@pytest.mark.parametrize("corner", ["top-right", "top-left", "bottom-right", "bottom-left"])
def test_screen_edges_have_no_rim_and_normals_point_out(corner):
    win = (400, 600)
    ops = shape.outline(shape.ShapeParams(300, 200, 28, 10, 0.6))
    edges = [("x", float(win[0]) if corner.endswith("right") else 0.0),
             ("y", float(win[1]) if corner.startswith("bottom") else 0.0)]
    segs = segments(_flat(ops, corner, win), edges)
    assert segs
    for x0, y0, x1, y1, n0x, n0y, n1x, n1y in segs:
        for axis, value in edges:
            i = 0 if axis == "x" else 1
            assert not (abs((x0, y0)[i] - value) < 0.6 and abs((x1, y1)[i] - value) < 0.6)
        assert math.hypot(n0x, n0y) == pytest.approx(1.0, abs=1e-6)
    # the long inner edge's normal points away from the screen corner
    cx = win[0] if corner.endswith("right") else 0.0
    mid = max(segs, key=lambda s: math.hypot(s[2] - s[0], s[3] - s[1]))
    mx = (mid[0] + mid[2]) / 2
    nx = (mid[4] + mid[6]) / 2
    ny = (mid[5] + mid[7]) / 2
    if abs(nx) > abs(ny):  # a vertical edge: the normal faces away from the anchoring side
        assert (nx < 0) == (cx > mx)


def test_closed_rect_keeps_all_edges():
    surface = cairo.ImageSurface(cairo.FORMAT_A8, 1, 1)
    cr = cairo.Context(surface)
    cr.rectangle(10, 10, 100, 50)
    segs = segments(cr.copy_path_flat(), [])
    assert len(segs) == 4
    top = next(s for s in segs if s[1] == s[3] == 10)
    # corner normals are averaged (diagonal); halfway along, the normal points up (outward)
    mx, my = (top[4] + top[6]) / 2, (top[5] + top[7]) / 2
    assert mx == pytest.approx(0.0, abs=1e-6) and my < 0


# ── kernel ───────────────────────────────────────────────────────────────────
native = pytest.importorskip("cornerwindow.native")
needs_cc = pytest.mark.skipif(shutil.which("cc") is None and shutil.which("gcc") is None,
                              reason="no C compiler for the glass kernel")


def _field(segs, w, h, bezel):
    lib = native.load()
    dist, nx, ny = ((ctypes.c_float * (w * h))() for _ in range(3))
    array = (native.Seg * len(segs))(*[native.Seg(*s) for s in segs])
    lib.cw_edge_field(array, len(segs), w, h, bezel, dist, nx, ny)
    return dist, nx, ny


@needs_cc
def test_edge_field_distance_and_normal():
    # one vertical edge at x = 10, outward normal +x
    dist, nx, ny = _field([(10.0, 0.0, 10.0, 20.0, 1.0, 0.0, 1.0, 0.0)], 20, 20, 6.0)
    i = 10 * 20 + 7  # pixel centre (7.5, 10.5): 2.5 px from the edge
    assert dist[i] == pytest.approx(2.5 ** 2, abs=1e-4)  # squared: the kernel roots only the rim
    assert (nx[i], ny[i]) == (pytest.approx(1.0), pytest.approx(0.0))
    assert dist[10 * 20 + 1] > 1e20  # beyond the bezel: untouched


@needs_cc
def test_shade_without_effects_returns_the_backdrop():
    lib = native.load()
    w = h = 8
    src = (ctypes.c_uint32 * (w * h))(*[0xFF000000 | (i * 3 << 16) | (i << 8) | (255 - i) for i in range(w * h)])
    mask = (ctypes.c_uint8 * (w * h))(*([255] * (w * h)))
    out = (ctypes.c_uint32 * (w * h))()
    dist, nx, ny = _field([], w, h, 4.0)
    p = native.Params()
    p.w, p.h, p.out_stride = w, h, w * 4
    p.src, p.src_w, p.src_h, p.src_stride, p.src_step = ctypes.addressof(src), w, h, w * 4, 1.0
    p.mask, p.mask_stride = ctypes.addressof(mask), w
    p.dist, p.nx, p.ny = ctypes.addressof(dist), ctypes.addressof(nx), ctypes.addressof(ny)
    p.bezel, p.saturation, p.lo_scale = 4.0, 1.0, 4.0
    lib.cw_shade(ctypes.byref(p), out)
    assert list(out) == list(src)


@needs_cc
def test_lens_samples_from_inside():
    """At the rim the backdrop is pulled in from further inside (a convex lens)."""
    lib = native.load()
    w, h = 40, 4
    # backdrop: a horizontal ramp, red = x * 6
    src = (ctypes.c_uint32 * (w * h))(*[0xFF000000 | ((x * 6) << 16) for _y in range(h) for x in range(w)])
    mask = (ctypes.c_uint8 * (w * h))(*([255] * (w * h)))
    out = (ctypes.c_uint32 * (w * h))()
    dist, nx, ny = _field([(40.0, -10.0, 40.0, 20.0, 1.0, 0.0, 1.0, 0.0)], w, h, 12.0)  # edge on the right
    p = native.Params()
    p.w, p.h, p.out_stride = w, h, w * 4
    p.src, p.src_w, p.src_h, p.src_stride, p.src_step = ctypes.addressof(src), w, h, w * 4, 1.0
    p.mask, p.mask_stride = ctypes.addressof(mask), w
    p.dist, p.nx, p.ny = ctypes.addressof(dist), ctypes.addressof(nx), ctypes.addressof(ny)
    p.bezel, p.disp, p.saturation, p.lo_scale = 12.0, 8.0, 1.0, 4.0
    lib.cw_shade(ctypes.byref(p), out)
    red = [(out[w + x] >> 16) & 255 for x in range(w)]
    assert red[39] < src[w + 39] >> 16 & 255  # the rim shows content from the left (inside)
    assert red[10] == (src[w + 10] >> 16) & 255  # the flat middle is untouched
