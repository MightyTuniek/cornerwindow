"""The island: one borderless, always-on-top, see-through window in a screen corner.

Collapsed it shows only a small black quarter-circle. Click it and the same
outline springs open into the panel; content pours out of the corner after it.
With the Liquid Glass theme the same outline is a lens over the live backdrop
(ui/glass.py), from the droplet trigger to the open panel.

Smoothness rules followed here:
  * the window is never resized or moved while animating (it is pre-sized to the
    largest the panel can get; everything outside the outline is transparent and
    click-through via an input shape that tracks the outline every frame)
  * all motion is spring physics driven by the frame clock, only while moving
  * springs keep velocity, so interrupting open/close reverses fluidly
"""

from __future__ import annotations

import time
from typing import Callable

import cairo
from gi.repository import Gdk, GLib, Gtk

from ..core import log
from ..core.activity import Activity, ActivityManager
from ..core.actions import ActionRegistry
from ..core.anim import Animator, Spring, clamp, lerp, progress
from ..config import Config
from ..platform.window.base import WindowBackend
from . import shape, theme
from .content import CardGrid
from .draw import text
from .glass import GlassConfig, GlassRenderer

_log = log.get("island")
OVERSHOOT_MARGIN = 44
CORNER_ZONE = 34  # clicking the very corner of the open panel closes it


class Island(Gtk.Window):
    def __init__(self, config: Config, backend: WindowBackend, activity: ActivityManager,
                 actions: ActionRegistry) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.config = config
        self.backend = backend
        self.activity = activity
        self.actions = actions
        self._read_config(config)
        g, a = self.g, self.a

        self.is_open = False
        self.pinned = False
        self.hovering = False
        self.fullscreen_hidden = False
        self.on_open_hooks: list[Callable[[], None]] = []
        self.on_close_hooks: list[Callable[[], None]] = []
        self.on_type: Callable[[str], bool] | None = None
        self.on_escape: list[Callable[[], bool]] = []  # return True to swallow Esc
        self.keep_open: list[Callable[[], bool]] = []  # e.g. "the settings window has focus"
        # Windows docked to the panel (settings, anything added later) follow it through these:
        # called after every change of panel_rect() (size, corner, monitor), never assumed.
        self.on_geometry: list[Callable[[], None]] = []
        self._geometry_key: tuple | None = None
        self._geometry_idle = 0
        self._opened_at = 0.0
        self._leave_timer = 0
        self._hover_timer = 0
        self._scroll_accum = 0.0
        self._last_region: tuple | None = None
        self.panel_w = 400
        self.panel_h = 400

        # -- window setup ---------------------------------------------------------------
        self.set_title("CornerWindow")
        self.set_app_paintable(True)
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None and screen.is_composited():
            self.set_visual(visual)
        else:
            _log.warning("No compositor: transparency unavailable, the island will look boxy")
        self.get_style_context().add_class("cw-window")
        backend.configure(self, g.get("window_type", "utility"))

        self.root = Gtk.EventBox()
        self.root.set_visible_window(False)
        self.root.set_above_child(True)  # collapsed: the trigger eats all input
        self.root.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.SCROLL_MASK
                             | Gdk.EventMask.SMOOTH_SCROLL_MASK | Gdk.EventMask.ENTER_NOTIFY_MASK
                             | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.root.get_style_context().add_class("cw-root")
        self.add(self.root)

        self.grid: CardGrid | None = None
        self.new_grid()

        # -- springs --------------------------------------------------------------------
        slow = float(a.get("slow_motion", 1.0))
        self.w = Spring(self.rest_r, precision=0.05)
        self.h = Spring(self.rest_r, precision=0.05)
        self.content = Spring(0.0, precision=0.002)  # staggered card entrance 0..1
        self.fade = Spring(0.0, precision=0.002)  # global content opacity
        self.visible_amt = Spring(1.0, response=0.3, damping_fraction=1.0, precision=0.002)
        self.glow = Spring(0.0, response=0.25, damping_fraction=1.0, precision=0.002)
        self.press_amt = Spring(0.0, response=0.5, damping_fraction=1.0, precision=0.003)
        self.anim = Animator(self, self._frame,
                             [self.w, self.h, self.content, self.fade, self.visible_amt, self.glow,
                              self.press_amt],
                             slow_motion=slow)

        # -- Liquid Glass ---------------------------------------------------------------
        self.palette = theme.current()
        self.glass: GlassRenderer | None = None
        self.glass_config = GlassConfig()
        self._press_at = (0.0, 0.0)
        self._dirty_timer = 0
        self._catch_up = 0
        self._last_dirty_draw = 0.0
        press = Gtk.GestureMultiPress.new(self.root)
        press.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)  # sees presses on any card
        press.set_button(0)
        press.connect("pressed", self._glass_pressed)
        press.connect("released", self._glass_released)
        self._press_gesture = press

        self.connect("draw", self._draw)
        self.connect("key-press-event", self._key)
        self.connect("focus-out-event", self._focus_out)
        self.root.connect("button-press-event", self._button)
        self.root.connect("scroll-event", self._scroll)
        self.root.connect("enter-notify-event", self._enter)
        self.root.connect("leave-notify-event", self._leave)
        activity.on_change(self._activity_changed)
        display = Gdk.Display.get_default()
        display.connect("monitor-added", lambda *_: GLib.timeout_add(500, self._replace))
        display.connect("monitor-removed", lambda *_: GLib.timeout_add(500, self._replace))
        screen = self.get_screen()
        if screen is not None:  # resolution or scale changes
            screen.connect("size-changed", lambda *_: GLib.timeout_add(200, self._replace))

    # == configuration ================================================================
    def _read_config(self, config: Config) -> None:
        self.config = config
        g, t, p, a = (config.section(s) for s in ("general", "trigger", "panel", "animation"))
        self.g, self.t, self.p, self.a = g, t, p, a
        self.corner = g.get("corner", "top-right")
        self.rest_r = float(t.get("radius", 20))
        self.hover_r = float(t.get("hover_radius", 26))
        self.pad = int(p.get("padding", 14))
        self.panel_radius = float(p.get("corner_radius", 28))
        self.ears = float(p.get("ears", 10))
        self.smoothing = float(p.get("smoothing", 0.6))
        self.bg = Gdk.RGBA()
        if not self.bg.parse(str(p.get("background", "#000000"))):
            self.bg.parse("#000000")

    # == theme =======================================================================
    def set_theme(self, palette: theme.Palette, glass_config: GlassConfig, backdrop=None) -> None:
        """Switch palette; `backdrop` (platform/backdrop) turns the glass on, None turns it off."""
        self.palette = palette
        self.glass_config = glass_config
        ctx = self.root.get_style_context()
        for cls in ("cw-glass", "cw-light"):
            (ctx.add_class if cls in palette.css_classes else ctx.remove_class)(cls)
        if backdrop is None and self.glass is not None:
            self.glass.close()
            self.glass = None
        elif backdrop is not None and self.glass is None:
            self.glass = GlassRenderer(backdrop.track(self._xid, self._glass_area, self._backdrop_dirty))
        if self.glass is not None:
            self.glass.invalidate()
        self.queue_draw()

    def glass_luma(self) -> float | None:
        return self.glass.last_luma if self.glass is not None else None

    def _xid(self) -> int:
        window = self.get_window()
        return window.get_xid() if window is not None and hasattr(window, "get_xid") else 0

    def _origin(self) -> tuple[int, int]:
        """The window's top-left on the root, device pixels (where backend.place put it)."""
        geometry = self._monitor().get_geometry()
        win_w = getattr(self, "win_w", self.get_allocated_width())
        win_h = getattr(self, "win_h", self.get_allocated_height())
        x = geometry.x if self.corner.endswith("left") else geometry.x + geometry.width - win_w
        y = geometry.y + geometry.height - win_h if self.corner.startswith("bottom") else geometry.y
        scale = self.get_scale_factor()
        return int(x * scale), int(y * scale)

    def _trigger_zone(self) -> tuple[int, int, int, int]:
        """Window-coordinate box around the collapsed trigger (activity bubbles included)."""
        size = int(max(self.w.value, self.h.value, self.hover_r, 40.0) + 12)
        win_w, win_h = self.get_allocated_width(), self.get_allocated_height()
        x = win_w - size if self.corner.endswith("right") else 0
        y = win_h - size if self.corner.startswith("bottom") else 0
        return x, y, size, size

    def _collapsed_settled(self) -> bool:
        return not self.is_open and self.openness() < 0.02 and not self.anim.running

    def _glass_area(self):
        """Root rectangle whose changes matter: the trigger while collapsed, else the window."""
        if self.visible_amt.target < 0.5:
            return None
        scale = self.get_scale_factor()
        ox, oy = self._origin()
        if self._collapsed_settled():
            x, y, w, h = self._trigger_zone()
        else:
            x, y, w, h = 0, 0, self.get_allocated_width(), self.get_allocated_height()
        return (ox + x * scale, oy + y * scale, w * scale, h * scale)

    def _backdrop_dirty(self) -> None:
        """Something behind us changed. A video behind may damage 60x a second; the
        backdrop follows at up to 30 fps open (video rate) and 15 fps collapsed."""
        if self.anim.running:
            return  # the animation redraws anyway (and catches up once settled)
        if self._dirty_timer:
            return
        rate = 15 if self._collapsed_settled() else 30
        wait = max(0, int((self._last_dirty_draw + 1 / rate - time.monotonic()) * 1000))
        self._dirty_timer = GLib.timeout_add(wait, self._dirty_draw)

    def _catch_up_backdrop(self) -> bool:
        """Redraw at full quality, with a fresh backdrop, once everything has settled."""
        if self.anim.running:
            return True  # check again shortly
        self._catch_up = 0
        self.queue_draw()
        return False

    def _dirty_draw(self) -> bool:
        self._dirty_timer = 0
        self._last_dirty_draw = time.monotonic()
        self.queue_draw()
        return False

    def _prefetch_glass(self) -> bool:
        if self.glass is not None:
            ox, oy = self._origin()
            scale = self.get_scale_factor()
            self.glass.prefetch((ox, oy, self.get_allocated_width() * scale, self.get_allocated_height() * scale),
                                self.glass_config.frost)
        return False

    def _glass_pressed(self, _gesture, _n, x, y) -> None:
        if self.glass is None or not self.is_open:
            return
        self._press_at = (x, y)
        self.press_amt.to(1.0, 0.18, 1.0)
        self.anim.kick()

    def _glass_released(self, *_args) -> None:
        if self.press_amt.target > 0:
            self.press_amt.to(0.0, 0.6, 1.0)
            self.anim.kick()

    def new_grid(self) -> CardGrid:
        """Replace the content grid (on startup and whenever the config changes)."""
        if self.grid is not None:
            self.root.remove(self.grid)
            self.grid.destroy()
        grid = CardGrid(self.corner, int(self.p.get("column_width", 330)), int(self.p.get("spacing", 10)),
                        float(self.a.get("stagger", 0.11)))
        grid.set_halign(Gtk.Align.END if self.corner.endswith("right") else Gtk.Align.START)
        grid.set_valign(Gtk.Align.END if self.corner.startswith("bottom") else Gtk.Align.START)
        for side in ("top", "bottom", "start", "end"):
            getattr(grid, f"set_margin_{side}")(self.pad)
        grid.clip_path = self._clip_for_grid
        grid.connect("size-allocate", self._grid_allocated)
        self.root.add(grid)
        self.grid = grid
        return grid

    def apply_config(self, config: Config) -> None:
        """Re-read everything from config. The caller then fills new_grid() and calls relayout()."""
        was_corner = self.corner
        self._read_config(config)
        self.anim.slow_motion = max(float(self.a.get("slow_motion", 1.0)), 0.05)
        if self.corner != was_corner:
            self._last_region = None
        if self._hides_for_fullscreen() is False and self.fullscreen_hidden:
            self.fullscreen_hidden = False
            self.visible_amt.to(1.0)

    def relayout(self) -> None:
        """After the grid was rebuilt: re-place the window and spring to the new size."""
        self.place()
        self._last_region = None
        if self.is_open:
            self.root.set_above_child(False)
            self.content.snap(1.0)
            self.fade.snap(1.0)
            self.w.to(self.panel_w)
            self.h.to(self.panel_h)
        else:
            self._retarget_collapsed()
        self.anim.kick()
        self.queue_draw()

    def _geometry_changed(self) -> None:
        """Tell docked windows that panel_rect() moved or resized: coalesced to one call per
        main-loop turn, and only when something actually differs."""
        if self._geometry_idle:
            return

        def emit() -> bool:
            self._geometry_idle = 0
            try:
                rect = self.panel_rect()
                geometry = self._monitor().get_geometry()
            except Exception:
                return False
            key = (rect.x, rect.y, rect.width, rect.height, self.corner,
                   geometry.x, geometry.y, geometry.width, geometry.height)
            if key == self._geometry_key:
                return False
            self._geometry_key = key
            for hook in list(self.on_geometry):
                try:
                    hook()
                except Exception:
                    _log.exception("Geometry hook failed")
            return False

        self._geometry_idle = GLib.idle_add(emit)

    def panel_rect(self) -> Gdk.Rectangle:
        """Where the open panel sits on screen (for placing the settings window beside it)."""
        geometry = self._monitor().get_geometry()
        rect = Gdk.Rectangle()
        rect.width, rect.height = int(self.panel_w), int(self.panel_h)
        rect.x = geometry.x if self.corner.endswith("left") else geometry.x + geometry.width - rect.width
        rect.y = geometry.y if self.corner.startswith("top") else geometry.y + geometry.height - rect.height
        return rect

    # == geometry ====================================================================
    def _monitor(self) -> Gdk.Monitor:
        display = Gdk.Display.get_default()
        wanted = self.g.get("monitor", "primary")
        if isinstance(wanted, int) and 0 <= wanted < display.get_n_monitors():
            return display.get_monitor(wanted)
        if isinstance(wanted, str) and wanted != "primary":
            for i in range(display.get_n_monitors()):
                mon = display.get_monitor(i)
                if wanted.lower() in (mon.get_model() or "").lower():
                    return mon
        return display.get_primary_monitor() or display.get_monitor(0)

    def place(self) -> None:
        self.root.show_all()
        # GTK3's preferred width already includes the grid's margins (the padding)
        _min, natural = self.grid.get_preferred_width()
        self.panel_w = natural
        geometry = self._monitor().get_geometry()
        self.win_w = self.panel_w + OVERSHOOT_MARGIN
        self.win_h = geometry.height
        self.max_panel_h = geometry.height - OVERSHOOT_MARGIN
        self.backend.place(self, self.corner, geometry, self.win_w, self.win_h)
        _log.debug("Placed %dx%d at %s of %dx%d", self.win_w, self.win_h, self.corner,
                   geometry.width, geometry.height)
        self._geometry_changed()

    def _replace(self) -> bool:
        self.place()
        self._last_region = None
        self._frame()
        return False

    def _grid_allocated(self, _grid, alloc) -> None:
        height = min(alloc.height + 2 * self.pad, getattr(self, "max_panel_h", 2000))
        if abs(height - self.panel_h) >= 1 and alloc.height > 1:
            self.panel_h = height
            self._geometry_changed()
            if self.is_open:
                self.h.to(height)
                self.anim.kick()

    # == state changes =================================================================
    def toggle(self, time_: int = 0) -> None:
        if self.is_open:
            self.close()
        else:
            self.open(time_)

    def open(self, time_: int = 0) -> None:
        if self.is_open or self.fullscreen_hidden:
            _log.debug("open ignored (open=%s, fullscreen_hidden=%s)", self.is_open, self.fullscreen_hidden)
            return
        _log.debug("open")
        self.is_open = True
        self._opened_at = time.monotonic()
        self._cancel_timers()
        for hook in list(self.on_open_hooks):
            hook()
        self.root.set_above_child(False)
        self.grid.invalidate()
        if self.glass is not None:
            self._prefetch_glass()  # before the first animated frame, not during it
        a = self.a
        self.w.to(self.panel_w, a.get("open_response", 0.42), a.get("open_damping", 0.78))
        self.h.to(self.panel_h, a.get("open_height_response", 0.5), a.get("open_damping", 0.78) + 0.04)
        self.fade.to(1.0, 0.3, 1.0)
        self.content.to(0.0)
        self.backend.take_focus(self, time_)
        self.anim.kick()

    def close(self, reason: str = "", hand_back: bool = True) -> None:
        """hand_back=False: don't refocus the window that had focus before opening
        (the user just switched to another one)."""
        if not self.is_open:
            return
        _log.debug("close %s", reason)
        self.is_open = False
        self.pinned = False
        self._cancel_timers()
        self.root.set_above_child(True)
        self.backend.release_focus(self, hand_back)
        a = self.a
        self.fade.to(0.0, a.get("content_out_response", 0.12), 1.0)
        self.content.to(0.0, 0.3, 1.0)
        target = self._collapsed_radius()
        self.w.to(target, a.get("close_response", 0.3), a.get("close_damping", 1.0))
        self.h.to(target, a.get("close_response", 0.3) * 1.08, a.get("close_damping", 1.0))
        for hook in list(self.on_close_hooks):
            hook()
        self.anim.kick()

    def set_pinned(self, pinned: bool) -> None:
        self.pinned = pinned

    def _hides_for_fullscreen(self) -> bool:
        return bool(self.g.get("hide_when_fullscreen", True)) and not self.g.get("always_on_top", False)

    def set_fullscreen_hidden(self, hidden: bool) -> None:
        if self.g.get("always_on_top", False):
            if hidden:
                self.backend.raise_above(self)
            return
        if not self._hides_for_fullscreen():
            return
        self.fullscreen_hidden = hidden
        _log.debug("fullscreen app focused: %s", hidden)
        if hidden and self.is_open:
            self.close("fullscreen")
        self.visible_amt.to(0.0 if hidden else 1.0)
        self.anim.kick()

    def _collapsed_radius(self) -> float:
        radius = self.hover_r if self.hovering else self.rest_r
        act = self.activity.current()
        if act is not None:
            auto = 38.0 if act.label else (31.0 if act.level is not None else 25.0)
            radius = max(radius, act.radius or auto)
        return radius

    def _retarget_collapsed(self) -> None:
        if self.is_open:
            return
        target = self._collapsed_radius()
        resp, damp = self.a.get("hover_response", 0.25), self.a.get("hover_damping", 0.9)
        if abs(self.w.target - target) > 0.01:
            self.w.to(target, resp, damp)
            self.h.to(target, resp, damp)
        self.glow.to(1.0 if self.hovering else 0.0)
        self.anim.kick()

    def _activity_changed(self) -> None:
        self._retarget_collapsed()
        self.queue_draw()

    # == frame =========================================================================
    def openness(self) -> float:
        return progress(self.w.value, max(self.rest_r, self.hover_r), self.panel_w)

    def _frame(self) -> None:
        opened = self.openness()
        if self.is_open and self.content.target < 1.0 and opened > 0.18:
            self.content.to(1.0, self.a.get("content_response", 0.55), 1.0)
        self.grid.reveal = self.content.value
        self.grid.opening = self.is_open
        self.grid.alpha = self.fade.value * self.visible_amt.value
        self.grid.live = (self.is_open and opened >= 0.999 and self.content.value >= 0.999
                          and self.fade.value >= 0.999)
        self._update_input_region()
        self.queue_draw()

    def _params(self) -> shape.ShapeParams:
        w, h = self.w.value, self.h.value
        opened = self.openness()
        radius = lerp(min(w, h), self.panel_radius, clamp(opened * 1.6))
        return shape.ShapeParams(w, h, radius, self.ears * opened, self.smoothing * clamp(opened * 2))

    def _apply_corner(self, cr) -> None:
        tx, ty, sx, sy = shape.corner_transform(self.corner, self.get_allocated_width(),
                                                self.get_allocated_height())
        cr.translate(tx, ty)
        cr.scale(sx, sy)

    def _clip_for_grid(self, cr) -> None:
        matrix = cr.get_matrix()
        alloc = self.grid.get_allocation()
        cr.translate(-alloc.x, -alloc.y)
        self._apply_corner(cr)
        shape.draw(cr, shape.outline(self._params()))
        cr.set_matrix(matrix)

    def _draw(self, _widget, cr) -> bool:
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        vis = self.visible_amt.value
        if vis <= 0.002:
            return False
        opened = self.openness()
        params = self._params()
        ops = shape.outline(params)

        glass = self._render_glass(params, ops, opened, vis) if self.glass is not None else None

        cr.save()
        self._apply_corner(cr)
        if opened > 0.02 and self.p.get("shadow", True):
            shape.draw(cr, ops)
            strength = self.palette.look.shadow if glass is not None else 1.0
            for width, alpha in ((26, 0.035), (14, 0.06), (6, 0.09)):
                cr.set_line_width(width)
                cr.set_source_rgba(0, 0, 0, alpha * opened * vis * strength)
                cr.stroke_preserve()
            cr.new_path()
        if glass is not None:
            surface, gx, gy = glass
            cr.save()
            cr.identity_matrix()
            cr.set_source_surface(surface, gx, gy)
            cr.paint_with_alpha(min(1.0, vis * 1.5))
            cr.restore()
            rim = 0.0
        else:
            shape.draw(cr, ops)
            cr.set_source_rgba(self.bg.red, self.bg.green, self.bg.blue, vis)
            cr.fill_preserve()
            rim = 0.09 * opened + 0.16 * self.glow.value * (1 - opened)
        if rim > 0.005:
            gradient = cairo.LinearGradient(0, 0, params.w, params.h)
            gradient.add_color_stop_rgba(0.0, 1, 1, 1, 0.0)
            gradient.add_color_stop_rgba(1.0, 1, 1, 1, rim * vis)
            cr.set_source(gradient)
            cr.set_line_width(1.0)
            cr.stroke()
        cr.new_path()

        act = self.activity.current()
        if act is not None and opened < 0.35:
            self._draw_activity(cr, act, min(params.w, params.h), (1 - opened / 0.35) * vis)
        cr.restore()
        return False  # let GTK draw the content grid on top

    def _render_glass(self, params: shape.ShapeParams, ops, opened: float, vis: float):
        """The glass surface for the current outline, or None (then the solid fill is used)."""
        win_w, win_h = self.get_allocated_width(), self.get_allocated_height()
        right, bottom = self.corner.endswith("right"), self.corner.startswith("bottom")
        f = max(0.0, min(params.ears, params.w * 0.5, params.h * 0.5))  # rounded-in corners stay inside
        bw, bh = params.w + f + 1, params.h + f + 1
        bbox = (win_w - bw if right else 0, win_h - bh if bottom else 0, bw, bh)
        edges = [("x", float(win_w) if right else 0.0), ("y", float(win_h) if bottom else 0.0)]
        scale = self.get_scale_factor()
        ox, oy = self._origin()
        if self._collapsed_settled():
            zx, zy, zw, zh = self._trigger_zone()
            capture = (ox + zx * scale, oy + zy * scale, zw * scale, zh * scale)
        elif self.anim.running:
            capture = (ox, oy, win_w * scale, win_h * scale)  # prefetched in open(); frozen meanwhile
        else:
            # settled open: only the panel (plus room for the blur), not the whole window height
            m = 32
            x0, y0 = max(0, int(bbox[0]) - m), max(0, int(bbox[1]) - m)
            x1, y1 = min(win_w, int(bbox[0] + bbox[2]) + m), min(win_h, int(bbox[1] + bbox[3]) + m)
            capture = (ox + x0 * scale, oy + y0 * scale, (x1 - x0) * scale, (y1 - y0) * scale)

        def path(cr) -> None:
            cr.save()
            self._apply_corner(cr)
            shape.draw(cr, ops)
            cr.restore()

        key = (round(params.w, 2), round(params.h, 2), round(params.radius, 2), round(params.ears, 2),
               round(params.smoothing, 3), self.corner, win_w, win_h)
        press = (*self._press_at, self.press_amt.value)
        # While anything animates, keep the backdrop still (refreshing costs a few ms and
        # nobody sees a backdrop a fraction of a second old mid-spring); catch up after.
        animating = self.anim.running
        bezel = min(26.0, 0.72 * min(params.w, params.h))
        try:
            result = self.glass.render(path_fn=path, shape_key=key, bbox=bbox, origin=(ox, oy), scale=scale,
                                     screen_edges=edges, palette=self.palette, config=self.glass_config,
                                     bezel=bezel, strength=vis, glow=self.glow.value * (1 - opened),
                                     press=press, capture_rect=capture, refresh=not animating,
                                     local_blur=self._collapsed_settled(),
                                     quality=0.5 if animating and opened > 0.05 else 1.0)
            if animating and self.glass is not None and not self._catch_up:
                # full resolution (and any backdrop change) once the spring has settled
                self._catch_up = GLib.timeout_add(30, self._catch_up_backdrop)
            return result
        except Exception:
            _log.exception("Glass rendering failed; falling back to the solid panel")
            self.glass.close()
            self.glass = None
            return None

    def _draw_activity(self, cr, act: Activity, r: float, alpha: float) -> None:
        import math

        ring = r - 7.0
        if act.level is not None and ring > 6:
            gap = 0.16
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.set_line_width(2.6)
            ink = self.palette.text
            cr.set_source_rgba(*ink[:3], 0.16 * alpha)
            cr.arc(0, 0, ring, gap, math.pi / 2 - gap)
            cr.stroke()
            level = clamp(act.level)
            if level > 0.005:
                cr.set_source_rgba(*self._activity_color(act.color), alpha)
                cr.arc(0, 0, ring, gap, gap + (math.pi / 2 - 2 * gap) * level)
                cr.stroke()
        centre = r * 0.38
        if act.dot:
            cr.set_source_rgba(*self._activity_color(act.color), alpha)
            cr.arc(centre, centre, 3.2, 0, 2 * math.pi)
            cr.fill()
        if act.label and r > 26:
            x, y = cr.user_to_device(centre, centre)
            cr.save()
            cr.identity_matrix()
            size = 11 if len(act.label) <= 2 else 9
            ink = self.palette.text
            text(cr, self, act.label, x, y, size=size, weight=700, rgba=(*ink[:3], alpha), align="center")
            cr.restore()

    def _activity_color(self, color) -> tuple[float, float, float]:
        """White indicators would vanish on light glass; use the palette's ink instead."""
        if self.palette.light and min(color[:3]) > 0.9:
            return self.palette.text[:3]
        return tuple(color[:3])

    def _update_input_region(self) -> None:
        win_w, win_h = self.get_allocated_width(), self.get_allocated_height()
        if self.visible_amt.target < 0.5:
            rects: list[tuple[int, int, int, int]] = []
        elif not self.is_open and self.openness() < 0.05:
            radius = (self.hover_r if self.hovering else self.rest_r) + 4
            radius = max(radius, self.w.value + 2)
            rects = shape.quarter_circle_rows(radius)
        else:
            rects = [(0, 0, int(self.w.value + 1), int(self.h.value + 1))]
        key = (tuple(rects), win_w, win_h)
        if key == self._last_region:
            return
        self._last_region = key
        right, bottom = self.corner.endswith("right"), self.corner.startswith("bottom")
        mapped = []
        for x, y, w, h in rects:
            wx = win_w - x - w if right else x
            wy = win_h - y - h if bottom else y
            mapped.append(cairo.RectangleInt(int(wx), int(wy), int(w), int(h)))
        self.input_shape_combine_region(cairo.Region(mapped))

    # == input =========================================================================
    def _canonical(self, x: float, y: float) -> tuple[float, float]:
        cx = self.get_allocated_width() - x if self.corner.endswith("right") else x
        cy = self.get_allocated_height() - y if self.corner.startswith("bottom") else y
        return cx, cy

    def _button(self, _w, event) -> bool:
        if event.type != Gdk.EventType.BUTTON_PRESS:
            return True
        if not self.is_open:
            if event.button == 1:
                self.open(event.time)
            elif event.button == 2:
                self.actions.run(self.t.get("middle_click"))
            elif event.button == 3:
                self.actions.run(self.t.get("right_click"))
            return True
        cx, cy = self._canonical(event.x, event.y)
        if cx < CORNER_ZONE and cy < CORNER_ZONE:
            self.close()
            return True
        return False

    def _scroll(self, _w, event) -> bool:
        if self.is_open:
            return False
        shift = bool(event.state & Gdk.ModifierType.SHIFT_MASK)
        name = self.t.get("shift_scroll" if shift else "scroll")
        if event.direction == Gdk.ScrollDirection.SMOOTH:
            _ok, dx, dy = event.get_scroll_deltas()
            self._scroll_accum += -dy if abs(dy) >= abs(dx) else dx
            steps = int(self._scroll_accum)
            if not steps:
                return True
            self._scroll_accum -= steps
        elif event.direction in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.RIGHT):
            steps = 1
        else:
            steps = -1
        self.actions.run(name, delta=steps)
        return True

    def _enter(self, _w, event) -> bool:
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        self.hovering = True
        if self.glass is not None and not self.is_open:
            GLib.idle_add(self._prefetch_glass, priority=GLib.PRIORITY_LOW)
        if self._leave_timer:
            GLib.source_remove(self._leave_timer)
            self._leave_timer = 0
        if not self.is_open:
            self._retarget_collapsed()
            if self.g.get("open_on", "click") == "hover" and not self._hover_timer:
                self._hover_timer = GLib.timeout_add(int(self.g.get("hover_open_delay_ms", 250)),
                                                     self._hover_open)
        return False

    def _hover_open(self) -> bool:
        self._hover_timer = 0
        if self.hovering:
            self.open(0)
        return False

    def _leave(self, _w, event) -> bool:
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        self.hovering = False
        if self._hover_timer:
            GLib.source_remove(self._hover_timer)
            self._hover_timer = 0
        if not self.is_open:
            self._retarget_collapsed()
            return False
        delay = int(self.g.get("close_on_leave_ms", 0))
        if delay > 0 and not self.pinned and not self._typing():
            self._leave_timer = GLib.timeout_add(delay, self._leave_close)
        return False

    def _leave_close(self) -> bool:
        self._leave_timer = 0
        if not self.hovering and not self.pinned and not self._typing():
            self.close()
        return False

    def _typing(self) -> bool:
        focus = self.get_focus()
        return isinstance(focus, (Gtk.Entry, Gtk.TextView))

    def _cancel_timers(self) -> None:
        for attr in ("_leave_timer", "_hover_timer"):
            source = getattr(self, attr)
            if source:
                GLib.source_remove(source)
                setattr(self, attr, 0)

    def _key(self, _w, event) -> bool:
        if not self.is_open:
            return False
        if event.keyval == Gdk.KEY_Escape:
            if not any(hook() for hook in list(self.on_escape)):
                self.close()
            return True
        if self._typing():
            return False
        char = chr(Gdk.keyval_to_unicode(event.keyval) or 0)
        ctrl = event.state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK)
        if char.isprintable() and char.strip() and not ctrl and self.on_type:
            return self.on_type(char)
        return False

    def _focus_out(self, *_args) -> bool:
        if not self.is_open or self.pinned or not self.g.get("close_on_focus_out", True):
            return False
        if time.monotonic() - self._opened_at < 0.35:
            return False
        GLib.timeout_add(60, self._focus_out_close)
        return False

    def _focus_out_close(self) -> bool:
        if self.is_open and not self.is_active() and not self.pinned \
                and Gtk.grab_get_current() is None \
                and not any(hook() for hook in list(self.keep_open)):
            self.close("focus-out")
        return False
