"""Apps: the taskbar, inside the panel. A strip of your favourite and running apps.

  click         switch to the app (click the app you're in to minimise it); with several
                windows, a list opens below to pick one (and close windows)
  middle click  a new window
  right click   the window list, also for a single window

Favourites come from the desktop (the apps pinned to its dash or taskbar). A window that asks
for attention puts an orange dot on the trigger. By default the card only appears while the
taskbar is hidden (`[experimental] hide_taskbar`); `[windows] show = "always"` keeps it.
"""

from __future__ import annotations

from gi.repository import Gdk, GLib, Gtk

from ..core.activity import ORANGE
from ..ui import theme
from ..ui.widgets import Card, IconButton, label
from ..ui.widgets.card import resolve_icon, set_active
from .base import Command, Module, module

ICON = 32


@module("windows", title="Apps", icon="apps",
        description="Switch between open apps and start your favourites (a taskbar in the panel)")
class Windows(Module):
    def setup(self) -> None:
        self.svc = None
        self.expanded: str | None = None  # the app whose window list is open
        self._signature = None
        self._render_id = 0
        self._dirty = True
        actions = self.ctx.actions
        actions.register("windows.cycle", self._cycle, "Switch to the next / previous window")
        actions.register("windows.show_desktop", lambda: self._service() and self._service().show_desktop(),
                         "Show the desktop")

    def _service(self):
        if self.svc is None:
            svc = self.ctx.services.windows
            if not svc.available():
                return None
            self.svc = svc
            svc.subscribe(self._changed)
            self._attention()
        return self.svc

    def available(self) -> bool:
        if self.config.get("show", "auto") != "always" and \
                not self.ctx.config.get("experimental.hide_taskbar", False):
            return False
        return self._service() is not None

    # -- widgets --------------------------------------------------------------------------
    def build(self):
        card = Card(spacing=6)
        self.strip = Gtk.FlowBox()
        self.strip.set_selection_mode(Gtk.SelectionMode.NONE)
        self.strip.set_homogeneous(True)
        self.strip.set_min_children_per_line(4)
        self.strip.set_max_children_per_line(16)
        self.strip.set_row_spacing(2)
        self.strip.set_column_spacing(2)
        self.strip.set_can_focus(False)
        self.strip.get_style_context().add_class("cw-apps")
        card.pack_start(self.strip, False, False, 0)
        self.empty = label("No open windows", "cw-dim cw-small")
        self.empty.set_no_show_all(True)
        card.pack_start(self.empty, False, False, 0)
        self.list_revealer = Gtk.Revealer()
        self.list_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.list_revealer.set_transition_duration(200)
        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        self.list_revealer.add(self.rows)
        card.pack_start(self.list_revealer, False, False, 0)
        self._tiles: dict[str, Gtk.Button] = {}
        self._signature = None
        self.expanded = None
        self._render()
        return card

    def on_open(self) -> None:
        if self._dirty:
            self._render()

    def on_close(self) -> None:
        if self.expanded is not None:
            self.expanded = None
            self._signature = None
            self._dirty = True  # fold the window list away for next time, without animating now

    def _changed(self) -> None:
        self._attention()
        if not self.ctx.island.is_open or not hasattr(self, "strip"):
            self._dirty = True  # nothing is drawn while collapsed; catch up on open
            return
        if self._render_id:
            return

        def later() -> bool:
            if self.ctx.island.anim.running:
                return True  # never lay out mid-animation; try again next tick
            self._render_id = 0
            self._render()
            return False

        self._render_id = GLib.timeout_add(60, later)

    def _render(self) -> None:
        if self.svc is None or not hasattr(self, "strip"):
            return
        self._dirty = False
        groups = self.svc.groups()
        signature = tuple(g.signature() for g in groups) + (self.expanded,)
        if signature == self._signature:
            self._refresh_titles(groups)  # same strip, maybe new titles
            return
        self._signature = signature
        for child in self.strip.get_children():
            self.strip.remove(child)
            child.destroy()
        self._tiles = {}
        for group in groups:
            tile = self._tile(group)
            self._tiles[group.key] = tile
            self.strip.add(tile)
        for child in self.strip.get_children():
            child.set_can_focus(False)
        self.strip.show_all()
        self.empty.set_visible(not groups)
        self._render_list(groups)

    def _tile(self, group) -> Gtk.Button:
        button = Gtk.Button()
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_can_focus(False)
        button.get_style_context().add_class("cw-app")
        set_active(button, group.active or group.key == self.expanded)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        image = _icon(group)
        box.pack_start(image, False, False, 0)
        dots = Gtk.DrawingArea()
        dots.set_size_request(ICON, 4)
        dots.connect("draw", self._draw_dots, group)
        box.pack_start(dots, False, False, 0)
        button.add(box)
        button.set_tooltip_text(_tooltip(group))
        button.connect("clicked", lambda _b, g=group: self._click(g))
        button.connect("button-release-event", self._button, group)
        return button

    @staticmethod
    def _draw_dots(area, cr, group) -> bool:
        count = min(len(group.windows), 3)
        if not count:
            return False
        p = theme.current()
        width = area.get_allocated_width()
        if group.urgent:
            cr.set_source_rgba(*ORANGE, 1.0)
        else:
            cr.set_source_rgba(*p.text[:3], 0.9 if group.active else 0.5)
        if count == 1 and group.active:
            # a short bar under the app you're in, like a running-app indicator
            x, w = width / 2 - 6, 12
            cr.arc(x + 1.5, 2, 1.5, 0, 6.2832)
            cr.arc(x + w - 1.5, 2, 1.5, 0, 6.2832)
            cr.fill()
            cr.rectangle(x + 1.5, 0.5, w - 3, 3)
            cr.fill()
            return False
        gap = 6
        start = width / 2 - (count - 1) * gap / 2
        for i in range(count):
            cr.arc(start + i * gap, 2, 1.6, 0, 6.2832)
            cr.fill()
        return False

    def _render_list(self, groups) -> None:
        for child in self.rows.get_children():
            self.rows.remove(child)
            child.destroy()
        group = next((g for g in groups if g.key == self.expanded), None)
        if group is None:
            self.expanded = None
            self.list_revealer.set_reveal_child(False)
            return
        self._row_labels = {}
        head = Gtk.Box(spacing=6)
        head.set_margin_top(4)
        head.pack_start(label(group.name, "cw-title", ellipsize=True), True, True, 0)
        if group.info is not None:
            head.pack_end(IconButton("add", lambda g=group: self._new_window(g), "New window",
                                     "cw-icon cw-plain cw-mini", 14), False, False, 0)
        self.rows.pack_start(head, False, False, 0)
        for window in group.windows:
            line = Gtk.Box(spacing=2)
            row = Gtk.Button()
            row.set_relief(Gtk.ReliefStyle.NONE)
            row.set_can_focus(False)
            row.get_style_context().add_class("cw-row")
            set_active(row, window.active)
            text = Gtk.Box(spacing=8)
            title = label(window.title, "cw-small" + (" cw-dim" if window.minimized else ""), ellipsize=True)
            self._row_labels[window.xid] = title
            text.pack_start(title, True, True, 0)
            detail = window.workspace or ("Minimised" if window.minimized else "")
            if detail:
                text.pack_end(label(detail, "cw-dim cw-small", xalign=1.0), False, False, 0)
            row.add(text)
            row.connect("clicked", lambda _b, w=window: self._switch(w))
            line.pack_start(row, True, True, 0)
            line.pack_end(IconButton("close", lambda w=window: self.svc.close(w), "Close window",
                                     "cw-icon cw-plain cw-mini", 12), False, False, 0)
            self.rows.pack_start(line, False, False, 0)
        self.rows.show_all()
        self.list_revealer.set_reveal_child(True)

    def _refresh_titles(self, groups) -> None:
        for group in groups:
            tile = self._tiles.get(group.key)
            if tile is not None:
                tile.set_tooltip_text(_tooltip(group))
            if group.key == self.expanded:
                for window in group.windows:
                    widget = getattr(self, "_row_labels", {}).get(window.xid)
                    if widget is not None and widget.get_text() != window.title:
                        widget.set_text(window.title)

    # -- clicks ---------------------------------------------------------------------------
    def _button(self, _button, event, group) -> bool:
        if event.button == Gdk.BUTTON_MIDDLE:
            self._new_window(group)
            return True
        if event.button == Gdk.BUTTON_SECONDARY:
            if group.windows:
                self._toggle_list(group)
            return True
        return False

    def _click(self, group) -> None:
        if not group.windows:
            self._new_window(group)
        elif len(group.windows) == 1:
            window = group.windows[0]
            if window.active and not window.minimized:
                self.svc.minimize(window)
                self.ctx.island.close("minimise", hand_back=False)
            else:
                self._switch(window)
        else:
            self._toggle_list(group)

    def _toggle_list(self, group) -> None:
        self.expanded = None if self.expanded == group.key else group.key
        # not right now: the clicked tile is destroyed by the re-render, and destroying a
        # button inside its own click handler can crash GTK 3
        GLib.idle_add(lambda: self._render() and False)

    def _switch(self, window) -> None:
        self.ctx.island.close("switch", hand_back=False)  # first, so it won't take focus back
        self.svc.activate(window)

    def _new_window(self, group) -> None:
        if self.svc.launch(group):
            self.ctx.island.close("launch", hand_back=False)

    def _cycle(self, delta: float = 1, **_kw) -> str | None:
        svc = self._service()
        if svc is None:
            return None
        window = svc.cycle(1 if delta >= 0 else -1)
        return window.title if window else None

    # -- attention ------------------------------------------------------------------------
    def _attention(self) -> None:
        if self.svc is None:
            return
        if self.config.get("attention_dot", True) and self.svc.urgent():
            self.ctx.activity.show("windows.attention", dot=True, color=ORANGE, persistent=True, priority=3)
        else:
            self.ctx.activity.clear("windows.attention")

    def commands(self) -> list[Command]:
        def desktop(_arg: str) -> str:
            if self.svc is None:
                return "Not available here"
            self.ctx.island.close("desktop", hand_back=False)
            return "Desktop" if self.svc.show_desktop() else "Windows back"

        return [Command(("desktop",), "Show the desktop", "desktop", desktop)]


def _icon(group) -> Gtk.Image:
    if group.icon is not None:
        image = Gtk.Image.new_from_gicon(group.icon, Gtk.IconSize.DIALOG)
    elif group.pixbuf is not None:
        pixbuf = group.pixbuf
        if pixbuf.get_width() != ICON:
            from gi.repository import GdkPixbuf

            pixbuf = pixbuf.scale_simple(ICON, ICON, GdkPixbuf.InterpType.BILINEAR)
        image = Gtk.Image.new_from_pixbuf(pixbuf)
    else:
        image = Gtk.Image.new_from_icon_name(resolve_icon("app"), Gtk.IconSize.DIALOG)
    image.set_pixel_size(ICON)
    if not group.windows:
        image.get_style_context().add_class("cw-app-idle")
    return image


def _tooltip(group) -> str:
    if not group.windows:
        return f"{group.name}\nClick to open"
    if len(group.windows) == 1:
        title = group.windows[0].title
        return group.name if title == group.name else f"{group.name}\n{title}"
    return f"{group.name} · {len(group.windows)} windows"
