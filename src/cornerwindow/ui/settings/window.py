"""A smaller black (or Liquid Glass) window that docks beside the open island and edits every option.

Edits are written straight into ~/.config/cornerwindow/config.toml (comments
kept) and applied live. Edits made in any config file while it's open show
up here immediately too.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import cairo
from gi.repository import Gdk, GLib, Gtk, Pango

from ...core import log
from ...core.anim import Animator, Spring
from ...platform import sensors
from ...platform.desktop import open_uri
from .. import theme
from ..glass import GlassConfig, GlassRenderer
from ..widgets import IconButton, label
from ..widgets.card import set_active
from . import editors
from .schema import SECTIONS, SKIP_SECTIONS, Field, Section, infer_field, section_for

if TYPE_CHECKING:
    from ...app import CornerWindowApp

_log = log.get("settings")
WIDTH = 480
GAP = 12
RADIUS = 24


class SettingsWindow(Gtk.Window):
    def __init__(self, app: "CornerWindowApp") -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.app = app
        self.shown = False
        self.rows: dict[tuple[str, str], dict] = {}
        self.pages: dict[str, Gtk.Widget] = {}
        self.nav: dict[str, Gtk.Button] = {}
        self.current = "general"
        self._status_id = 0
        self._reposition_id = 0
        self._repositions = 0

        self.set_title("CornerWindow settings")
        self.set_app_paintable(True)
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None and screen.is_composited():
            self.set_visual(visual)
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.stick()
        self.get_style_context().add_class("cw-window")
        self.connect("draw", self._draw)
        self.connect("key-press-event", self._key)
        self.connect("focus-out-event", self._focus_out)
        self.connect("configure-event", self._configured)
        self.connect("delete-event", lambda *_a: self.hide_animated() or True)

        self.alpha = Spring(0.0, response=0.26, damping_fraction=1.0, precision=0.002)
        self.slide = Spring(0.0, response=0.34, damping_fraction=0.82, precision=0.05)
        self.anim = Animator(self, self._frame, [self.alpha, self.slide])

        self.palette = theme.current()
        self.glass: GlassRenderer | None = None
        self.glass_config = GlassConfig()

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.root_box = root
        root.get_style_context().add_class("cw-root")
        root.get_style_context().add_class("cw-settings")
        for side in ("top", "bottom", "start", "end"):
            getattr(root, f"set_margin_{side}")(16)
        self.add(root)

        header = Gtk.Box(spacing=8)
        header.pack_start(label("Settings", "cw-settings-title"), False, False, 0)
        header.pack_end(IconButton("clear", self.hide_animated, "Close (Esc)", "cw-icon cw-plain", 14), False, False, 0)
        header.pack_end(IconButton("document-edit-symbolic", self._open_file, "Open your config file",
                                   "cw-icon cw-plain", 14), False, False, 0)
        root.pack_start(header, False, False, 0)

        body = Gtk.Box(spacing=12)
        root.pack_start(body, True, True, 0)
        nav_scroll = Gtk.ScrolledWindow()
        nav_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        nav_scroll.set_size_request(118, -1)
        self.nav_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        nav_scroll.add(self.nav_box)
        body.pack_start(nav_scroll, False, False, 0)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(140)
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.add(self.stack)
        body.pack_start(self.scroll, True, True, 0)

        footer = Gtk.Box(spacing=8)
        self.status = label("", "cw-dim cw-small", ellipsize=True)
        footer.pack_start(self.status, True, True, 0)
        root.pack_start(footer, False, False, 0)

        self._build_nav()

    # == navigation ======================================================================
    def _sections(self) -> list[Section]:
        known = [s for s in SECTIONS]
        ids = {s.id for s in known}
        for name, value in self.app.config.data.items():
            if name not in ids and name not in SKIP_SECTIONS and isinstance(value, dict):
                known.append(Section(name, name.replace("_", " ").title(), "settings", []))
        return known

    def _build_nav(self) -> None:
        for child in self.nav_box.get_children():
            self.nav_box.remove(child)
        self.nav.clear()
        entries = [(s.id, s.title) for s in self._sections()] + [("files", "Files")]
        for sid, title in entries:
            button = Gtk.Button()
            button.set_relief(Gtk.ReliefStyle.NONE)
            button.set_can_focus(False)
            button.get_style_context().add_class("cw-row")
            button.get_style_context().add_class("cw-nav")
            text = Gtk.Label(label=title)
            text.set_xalign(0)
            text.set_ellipsize(Pango.EllipsizeMode.END)
            button.add(text)
            button.connect("clicked", lambda _b, s=sid: self.show_page(s))
            self.nav_box.pack_start(button, False, False, 0)
            self.nav[sid] = button
        self.nav_box.show_all()

    def show_page(self, sid: str) -> None:
        if sid not in self.pages:
            page = self._files_page() if sid == "files" else self._page(sid)
            page.show_all()
            self.pages[sid] = page
            self.stack.add_named(page, sid)
        self.current = sid
        self.stack.set_visible_child_name(sid)
        self.scroll.get_vadjustment().set_value(0)
        for key, button in self.nav.items():
            set_active(button, key == sid)

    # == pages ===========================================================================
    def _context(self) -> dict:
        return {
            "actions": self.app.actions.names(),
            "modules": self.app.host.known_ids(),
            "catalog": self.app.host.catalog,
            "choices": _switchlist_choices,
            "sensors": [f"{s.chip}/{s.label}" for s in sensors.temperature_sensors()],
            "limits": self.app.config.get("display.limits", {}) or {},
            "background": lambda: self.app.island.bg,
        }

    def _page(self, sid: str) -> Gtk.Widget:
        section = section_for(sid) or Section(sid, sid.title(), "settings", [])
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.set_margin_end(6)
        if section.help:
            box.pack_start(_wrap(label(section.help, "cw-dim cw-small")), False, False, 0)
        fields = list(section.fields)
        described = {f.key for f in fields}
        for key, value in (self.app.config.section(sid) or {}).items():
            if key not in described:
                fields.append(infer_field(key, value))
        ctx = self._context()
        for field in fields:
            box.pack_start(self._row(sid, field, ctx), False, False, 0)
        if not fields:
            box.pack_start(label("Nothing to set here yet.", "cw-dim"), False, False, 0)
        return box

    def _value(self, sid: str, field: Field) -> Any:
        if field.keys:
            return {key: self.app.config.get(f"{sid}.{key}") for key in field.keys}
        return self.app.config.get(f"{sid}.{field.key}")

    def _row(self, sid: str, field: Field, ctx: dict) -> Gtk.Widget:
        value = self._value(sid, field)
        ctx = dict(ctx, section=sid, key=field.key)
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        wide = field.kind in ("columns", "presets", "dict", "toml", "list", "modules", "limits", "layout",
                              "switchlist", "place_search")
        line = Gtk.Box(spacing=8)
        title = label(field.label, "cw-setting-label")
        title.set_ellipsize(Pango.EllipsizeMode.END)
        line.pack_start(title, False, False, 0)
        badge = label("", "cw-badge")
        badge.set_valign(Gtk.Align.CENTER)
        line.pack_start(badge, False, False, 0)
        reset = IconButton("edit-undo-symbolic", lambda: self._reset(sid, field), "Back to default",
                           "cw-icon cw-plain", 12)
        reset.set_no_show_all(True)
        line.pack_start(reset, False, False, 0)
        editor = editors.make(field, value, lambda v: self._changed(sid, field, v), ctx)
        if not wide:
            editor.widget.set_halign(Gtk.Align.END)
            line.pack_end(editor.widget, False, False, 0)
        row.pack_start(line, False, False, 0)
        if wide:
            row.pack_start(editor.widget, False, False, 0)
        help_text = field.help + ("  Applies after restart." if field.restart else "")
        if help_text.strip():
            row.pack_start(_wrap(label(help_text.strip(), "cw-dim cw-small")), False, False, 0)
        self.rows[(sid, field.key)] = {"badge": badge, "reset": reset, "editor": editor, "field": field}
        self._update_badge(sid, field.key)
        return row

    def _files_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.pack_start(_wrap(label("Settings are layered; later files win. Everything you change here is "
                                   "written to your file, and edits to any file appear here live.",
                                   "cw-dim cw-small")), False, False, 0)
        config = self.app.config
        paths = list(config.sources)
        if config.user_file and config.user_file not in paths:
            paths.append(config.user_file)
        for index, path in enumerate(paths):
            title = "Yours" if path == config.user_file else ("Defaults" if index == 0 else "This PC")
            line = Gtk.Box(spacing=8)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            text.pack_start(label(title, "cw-setting-label"), False, False, 0)
            text.pack_start(label(str(path), "cw-dim cw-small", ellipsize=True), False, False, 0)
            line.pack_start(text, True, True, 0)
            line.pack_end(IconButton("document-edit-symbolic", lambda p=path: self._open(p), "Open",
                                     "cw-icon", 14), False, False, 0)
            box.pack_start(line, False, False, 0)

        box.pack_start(label("Your file", "cw-title"), False, False, 4)
        self.raw = Gtk.TextView()
        self.raw.set_monospace(True)
        self.raw.get_style_context().add_class("cw-notes")
        self.raw.set_wrap_mode(Gtk.WrapMode.NONE)
        self.raw.get_buffer().set_text(self.app.writer.read_text())
        # unwrapped, the longest line would set the whole window's minimum width
        raw_scroll = Gtk.ScrolledWindow()
        raw_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        raw_scroll.set_size_request(-1, 260)
        raw_scroll.add(self.raw)
        box.pack_start(raw_scroll, False, False, 0)
        actions = Gtk.Box(spacing=6)
        save = Gtk.Button(label="Save file")
        save.get_style_context().add_class("cw-chip")
        save.set_relief(Gtk.ReliefStyle.NONE)
        save.connect("clicked", lambda *_a: self._save_raw())
        actions.pack_start(save, False, False, 0)
        box.pack_start(actions, False, False, 0)
        return box

    # == writes ==========================================================================
    def _changed(self, sid: str, field: Field, value: Any) -> None:
        if value is None:
            self._reset(sid, field)
            return
        base = self.app.config.base.get(sid, {})
        try:
            if field.keys:
                values = {k: v for k, v in value.items() if k in field.keys}
                self.app.writer.set_many(sid, values, {k: base.get(k) for k in values})
            else:
                self.app.writer.set(sid, field.key, value, base.get(field.key))
        except Exception as exc:  # bad value or unwritable file: say so, keep running
            _log.warning("Could not save %s.%s: %s", sid, field.key, exc)
            self.flash(f"Not saved: {exc}")
            return
        self.flash(f"Saved {field.label.lower()}" + (" (restart to apply)" if field.restart else ""))
        others = [k for k in field.keys if k != field.key]  # rows of their own on this page
        if others:
            self.app.schedule_reload(then=lambda: [self._refresh_row(sid, k) for k in others])
        else:
            self.app.schedule_reload()

    def _reset(self, sid: str, field: Field) -> None:
        try:
            self.app.writer.reset(sid, *(field.keys or (field.key,)))
        except Exception as exc:
            self.flash(f"Not saved: {exc}")
            return
        self.flash(f"{field.label} back to default")
        self.app.schedule_reload(then=lambda: self._refresh_row(sid, field.key))

    def _save_raw(self) -> None:
        buf = self.raw.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        try:
            self.app.writer.write_text(text)
        except Exception as exc:
            self.flash(f"Not saved — {exc}")
            return
        self.flash("File saved")
        self.app.schedule_reload(then=self.refresh)

    # == sync from config ================================================================
    def after_reload(self, external: bool) -> None:
        """Called by the app after every reload; external = a file was edited elsewhere."""
        if self.shown:
            self._position()
        if external:
            self.refresh()
        else:
            for sid, key in list(self.rows):
                self._update_badge(sid, key)

    def refresh(self) -> None:
        """Rebuild pages from the current config (keeps the page you're on)."""
        for page in list(self.pages.values()):
            self.stack.remove(page)
            page.destroy()
        self.pages.clear()
        self.rows.clear()
        self._build_nav()
        if self.shown:
            self.show_page(self.current if self.current in self.nav else "general")

    def _refresh_row(self, sid: str, key: str) -> None:
        row = self.rows.get((sid, key))
        if row:
            row["editor"].set(self._value(sid, row["field"]))
            self._update_badge(sid, key)

    def _update_badge(self, sid: str, key: str) -> None:
        row = self.rows.get((sid, key))
        if not row:
            return
        keys = row["field"].keys or (key,)
        origins = [self.app.config.origin(sid, k) for k in keys]
        origin = next((o for o in ("yours", "machine") if o in origins), origins[0])
        text = {"yours": "changed", "machine": "this PC"}.get(origin, "")
        row["badge"].set_text(text)
        ctx = row["badge"].get_style_context()
        for cls in ("yours", "machine"):
            ctx.remove_class(cls)
        if origin in ("yours", "machine"):
            ctx.add_class(origin)
        row["reset"].set_visible(origin == "yours")

    def flash(self, text: str) -> None:
        self.status.set_text(text)
        if self._status_id:
            GLib.source_remove(self._status_id)

        def clear() -> bool:
            self._status_id = 0
            self.status.set_text(self._default_status())
            return False

        self._status_id = GLib.timeout_add(2500, clear)

    def _default_status(self) -> str:
        path = self.app.config.user_file
        return f"Saves to {str(path).replace(str(GLib.get_home_dir()), '~')}" if path else ""

    # == showing / hiding ================================================================
    def toggle(self) -> None:
        if self.shown:
            self.hide_animated()
        else:
            self.show_beside()

    def show_beside(self) -> None:
        island = self.app.island
        if not island.is_open:
            island.open()
        self.status.set_text(self._default_status())
        self.shown = True
        self._repositions = 0
        self.set_opacity(0.0)
        # build the page first so _position() measures the real content width
        self.get_child().show_all()
        self.show_page(self.current)
        right = self._position()
        self.show_all()
        self.present_with_time(Gtk.get_current_event_time())
        self.slide.snap(18.0 if right else -18.0)
        self.slide.to(0.0)
        self.alpha.to(1.0)
        self.anim.kick()

    def _position(self) -> bool:
        """Dock beside the island's panel. Returns True when the island is on the right."""
        island = self.app.island
        rect = island.panel_rect()
        geometry = island._monitor().get_geometry()
        height = int(max(520, min(rect.height, geometry.height - 2 * GAP, 820)))
        right = island.corner.endswith("right")
        # free space between the panel and the far screen edge, minus a gap on both sides
        if right:
            avail = rect.x - geometry.x - 2 * GAP
        else:
            avail = geometry.x + geometry.width - (rect.x + rect.width) - 2 * GAP
        # GTK grows the window to its content's minimum width, so place it by the width it
        # will really have. If that doesn't fit beside the panel, scroll sideways instead.
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        need = max(WIDTH, self.get_preferred_width()[0])
        if need > avail:
            self.scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            need = max(min(WIDTH, avail), self.get_preferred_width()[0])
        width = int(need)
        x = rect.x - GAP - width if right else rect.x + rect.width + GAP
        y = rect.y + (GAP if island.corner.startswith("top") else rect.height - height - GAP)
        y = max(geometry.y, min(y, geometry.y + geometry.height - height))
        self._placed = (int(x), int(y), width, height)
        self.set_default_size(width, height)
        self.resize(width, height)
        self.move(int(x), int(y))
        return right

    def _configured(self, _w, event) -> bool:
        """Re-dock when the window ends up somewhere else than planned, e.g. a lazily built
        page made it wider, or the window manager placed it on its own."""
        planned = getattr(self, "_placed", None)
        # a few tries per opening, so we never fight a window manager that insists
        if self.shown and planned and not self._reposition_id and self._repositions < 4:
            x, _y, width, _h = planned
            if event.width != width or (event.send_event and event.x != x):
                def again() -> bool:
                    self._reposition_id = 0
                    if self.shown:
                        self._position()
                    return False

                self._repositions += 1
                self._reposition_id = GLib.idle_add(again)
        return False

    def hide_animated(self) -> None:
        if not self.shown:
            return
        self.shown = False
        self.alpha.to(0.0, 0.16, 1.0)
        self.anim.kick()
        island = self.app.island
        if island.is_open:
            island.present_with_time(Gtk.get_current_event_time())

    def _frame(self) -> None:
        self.set_opacity(max(0.0, min(1.0, self.alpha.value)))
        if not self.shown and self.alpha.value <= 0.01 and self.get_visible():
            self.hide()
        self.queue_draw()

    # == events ==========================================================================
    def _key(self, _w, event) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self.hide_animated()
            return True
        return False

    def _focus_out(self, *_a) -> bool:
        GLib.timeout_add(90, self._check_focus)
        return False

    def _check_focus(self) -> bool:
        island = self.app.island
        # An open dropdown/menu is its own popup window holding a GTK grab, so focus
        # "leaves" this window while the user is still picking something in it.
        if Gtk.grab_get_current() is not None:
            return False
        if self.shown and not self.is_active() and not island.is_active() and not island.pinned:
            self.hide_animated()
            island.close("focus-out")
        return False

    def _open_file(self) -> None:
        from ... import config as config_mod

        self._open(config_mod.ensure_user_file())

    def _open(self, path) -> None:
        open_uri(path.as_uri())

    # == theme =========================================================================
    def set_theme(self, palette: theme.Palette, glass_config: GlassConfig, backdrop=None) -> None:
        self.palette = palette
        self.glass_config = glass_config
        ctx = self.root_box.get_style_context()
        for cls in ("cw-glass", "cw-light"):
            (ctx.add_class if cls in palette.css_classes else ctx.remove_class)(cls)
        if backdrop is None and self.glass is not None:
            self.glass.close()
            self.glass = None
        elif backdrop is not None and self.glass is None:
            self.glass = GlassRenderer(backdrop.track(self._xid, self._glass_area, self.queue_draw))
        if self.glass is not None:
            self.glass.invalidate()
        self.queue_draw()

    def _xid(self) -> int:
        window = self.get_window()
        return window.get_xid() if window is not None and hasattr(window, "get_xid") else 0

    def _origin(self) -> tuple[int, int] | None:
        window = self.get_window()
        if window is None:
            return None
        result = window.get_origin()
        x, y = result[-2:]
        scale = self.get_scale_factor()
        return int(x * scale), int(y * scale)

    def _glass_area(self):
        if not self.get_visible():
            return None
        origin = self._origin()
        if origin is None:
            return None
        scale = self.get_scale_factor()
        return (*origin, self.get_allocated_width() * scale, self.get_allocated_height() * scale)

    def _draw_glass(self, cr, x: float, width: float, h: float) -> bool:
        origin = self._origin()
        if origin is None:
            return False
        scale = self.get_scale_factor()
        try:
            result = self.glass.render(
                path_fn=lambda c: _squircle(c, x, 0, width, h, RADIUS), shape_key=(round(x, 2), width, h),
                bbox=(x, 0, width, h), origin=origin, scale=scale, screen_edges=[], palette=self.palette,
                config=self.glass_config, bezel=20.0, capture_rect=self._glass_area())
        except Exception:
            _log.exception("Glass rendering failed; falling back to the solid window")
            self.glass.close()
            self.glass = None
            return False
        if result is None:
            return False
        surface, gx, gy = result
        cr.set_source_surface(surface, gx, gy)
        cr.paint()
        return True

    # == drawing =========================================================================
    def _draw(self, _w, cr) -> bool:
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        w, h = self.get_allocated_width(), self.get_allocated_height()
        x = max(self.slide.value, 0.0)
        width = w - abs(self.slide.value)
        if self.glass is not None and self._draw_glass(cr, x, width, h):
            return False
        _squircle(cr, x, 0, width, h, RADIUS)
        bg = self.app.island.bg
        cr.set_source_rgba(bg.red, bg.green, bg.blue, 1.0)
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.09)
        cr.set_line_width(1)
        cr.stroke()
        return False


def _switchlist_choices(section: str, key: str) -> list[tuple[str, str, str]]:
    """(id, title, description) for every item a switch list can hold."""
    if section == "toggles":
        from ...modules.toggles import _TOGGLES

        return [(tid, cls.title, getattr(cls, "description", "")) for tid, cls in _TOGGLES.items()]
    if section == "power":
        from ...modules.power import ACTIONS

        return [(pid, title, "") for pid, (_icon, title, _fn, _color) in ACTIONS.items()]
    return []


def _wrap(widget: Gtk.Label) -> Gtk.Label:
    widget.set_line_wrap(True)
    widget.set_max_width_chars(30)
    widget.set_xalign(0)
    return widget


def _squircle(cr, x: float, y: float, w: float, h: float, r: float) -> None:
    """Rounded rectangle with slightly continuous corners."""
    r = min(r, w / 2, h / 2)
    k = r * 0.62  # handle length; > 0.5523 gives the softer iOS-like curvature
    cr.new_path()
    cr.move_to(x + r, y + 0.5)
    cr.line_to(x + w - r, y + 0.5)
    cr.curve_to(x + w - r + k, y + 0.5, x + w - 0.5, y + r - k, x + w - 0.5, y + r)
    cr.line_to(x + w - 0.5, y + h - r)
    cr.curve_to(x + w - 0.5, y + h - r + k, x + w - r + k, y + h - 0.5, x + w - r, y + h - 0.5)
    cr.line_to(x + r, y + h - 0.5)
    cr.curve_to(x + r - k, y + h - 0.5, x + 0.5, y + h - r + k, x + 0.5, y + h - r)
    cr.line_to(x + 0.5, y + r)
    cr.curve_to(x + 0.5, y + r - k, x + r - k, y + 0.5, x + r, y + 0.5)
    cr.close_path()
