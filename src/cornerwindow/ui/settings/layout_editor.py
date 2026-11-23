"""The Features page: every card with an on/off switch, dragged between the top row, the
columns, the drawer and "not in the panel". Also the reorderable switch list used for
quick toggles and power buttons.

The logic lives in modules/layout.py; this file is only the GTK side.
"""

from __future__ import annotations

from typing import Any, Callable

import cairo
from gi.repository import Gdk, GLib, Gtk

from ...modules.layout import Layout, allowed, preset
from .. import theme
from ..widgets import chip, label
from ..widgets.card import IconButton, resolve_icon

TARGET = "CW_SETTINGS_ROW"
_TARGETS = [Gtk.TargetEntry.new(TARGET, Gtk.TargetFlags.SAME_APP, 0)]


# ── shared pieces ───────────────────────────────────────────────────────────
def _row(title: str, description: str, icon: str | None, on: bool, on_switch: Callable[[bool], None],
         drag_id: str, on_menu: Callable[[Gdk.EventButton], None] | None = None) -> Gtk.Widget:
    """handle · icon · title/description · switch. The left part drags, the switch flips."""
    row = Gtk.Box(spacing=8)
    row.get_style_context().add_class("cw-layout-row")
    if not on:
        row.get_style_context().add_class("cw-off")
    grip = Gtk.EventBox()
    grip.set_visible_window(False)
    left = Gtk.Box(spacing=8)
    handle = Gtk.Image.new_from_icon_name(resolve_icon("drag"), Gtk.IconSize.MENU)
    handle.get_style_context().add_class("cw-dim")
    left.pack_start(handle, False, False, 0)
    if icon is not None:  # None: a plain list without icons
        image = Gtk.Image.new_from_icon_name(resolve_icon(icon or "settings"), Gtk.IconSize.MENU)
        image.set_pixel_size(16)
        left.pack_start(image, False, False, 0)
    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    text.get_style_context().add_class("cw-layout-text")
    text.pack_start(label(title, "cw-setting-label", ellipsize=True), False, False, 0)
    if description:
        text.pack_start(label(description, "cw-dim cw-small", ellipsize=True), False, False, 0)
        grip.set_tooltip_text(description)
    left.pack_start(text, True, True, 0)
    grip.add(left)
    row.pack_start(grip, True, True, 0)
    switch = Gtk.Switch()
    switch.set_valign(Gtk.Align.CENTER)
    switch.set_active(on)
    switch.connect("notify::active", lambda s, _p: on_switch(s.get_active()))
    row.pack_start(switch, False, False, 0)

    grip.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, _TARGETS, Gdk.DragAction.MOVE)
    grip.connect("drag-data-get", lambda _w, _c, data, _i, _t: data.set(data.get_target(), 8, drag_id.encode()))
    grip.connect("drag-begin", lambda _w, context: _drag_icon(row, context))
    if on_menu is not None:
        grip.connect("button-press-event",
                     lambda _w, event: (event.button == 3 and (on_menu(event) or True)) or False)
    row.mid = drag_id
    return row


def _drag_icon(row: Gtk.Widget, context) -> None:
    alloc = row.get_allocation()
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(alloc.width, 1), max(alloc.height, 1))
    cr = cairo.Context(surface)
    palette = theme.current()
    cr.set_source_rgba(*palette.track[:3], 0.9)
    cr.paint()
    row.draw(cr)
    Gtk.drag_set_icon_surface(context, surface)


def _zone(rows: list[Gtk.Widget], on_drop: Callable[[str, int], None], empty: str) -> Gtk.Widget:
    """A drop target listing `rows`; a drop lands at the gap nearest the pointer."""
    zone = Gtk.EventBox()
    zone.set_visible_window(False)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    box.get_style_context().add_class("cw-layout-zone")
    for row in rows:
        box.pack_start(row, False, False, 0)
    if not rows:
        hint = label(empty, "cw-dim cw-small")
        hint.set_margin_top(6)
        hint.set_margin_bottom(6)
        hint.set_margin_start(8)
        box.pack_start(hint, False, False, 0)
    zone.add(box)
    zone.drag_dest_set(Gtk.DestDefaults.ALL, _TARGETS, Gdk.DragAction.MOVE)

    def index_at(y: int) -> int:
        for i, row in enumerate(rows):
            coords = row.translate_coordinates(zone, 0, 0)
            if coords and y < coords[1] + row.get_allocated_height() / 2:
                return i
        return len(rows)

    def received(_w, _context, _x, y, data, _info, _time) -> None:
        raw = data.get_data()
        if raw:
            index = index_at(y)
            GLib.idle_add(lambda: on_drop(raw.decode(), index) and False)  # after the drag ends

    zone.connect("drag-data-received", received)
    zone.connect("drag-motion", lambda *_a: box.get_style_context().add_class("cw-drop") or False)
    zone.connect("drag-leave", lambda *_a: box.get_style_context().remove_class("cw-drop"))
    return zone


def _heading(text: str, *extra: Gtk.Widget) -> Gtk.Widget:
    line = Gtk.Box(spacing=6)
    line.pack_start(label(text, "cw-title"), False, False, 0)
    for widget in extra:
        line.pack_end(widget, False, False, 0)
    return line


def _menu(anchor: Gtk.Widget, event, items: list[tuple[str, Callable[[], None]]], ctx: dict) -> None:
    from .editors import theme_popup

    menu = Gtk.Menu()
    for text, fn in items:
        item = Gtk.MenuItem(label=text)
        item.connect("activate", lambda _i, f=fn: GLib.idle_add(lambda: f() and False))
        menu.append(item)
    menu.show_all()
    menu.attach_to_widget(anchor, None)
    theme_popup(anchor, ctx)
    menu.popup_at_pointer(event)


def later(fn: Callable, *rest) -> None:
    """Run fn(*args) and then each trailing callable, once the current signal is done
    (re-rendering destroys the button that was just clicked)."""
    args = [a for a in rest if not callable(a)]
    after = [a for a in rest if callable(a)]

    def run() -> bool:
        fn(*args)
        for callback in after:
            callback()
        return False

    GLib.idle_add(run)


# ── the card layout ─────────────────────────────────────────────────────────
def layout_editor(field, value, on_change, ctx):
    from .editors import Editor, _emit

    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    state: dict[str, Any] = {"layout": Layout(), "catalog": None}
    editor = Editor(outer)

    def catalog() -> dict:
        if state["catalog"] is None:
            source = ctx.get("catalog")
            state["catalog"] = source() if callable(source) else (source or {})
        return state["catalog"]

    def info(mid: str) -> dict:
        return catalog().get(mid) or {"title": mid, "icon": "", "area": "main",
                                      "description": "Not found (check the id or the module folder)"}

    def commit() -> None:
        _emit(editor, on_change, state["layout"].to_config())
        render()

    def drop(mid: str, dest, index: int) -> None:
        if state["layout"].move(mid, dest, index, info(mid)["area"]):
            commit()

    def switched(mid: str, on: bool) -> None:
        layout: Layout = state["layout"]
        if layout.visible(mid) == on:
            return
        layout.set_visible(mid, on, info(mid)["area"])
        GLib.idle_add(lambda: commit() and False)  # not while the switch is mid-signal

    def menu_for(mid: str, dest, event, anchor) -> None:
        layout: Layout = state["layout"]
        area = info(mid)["area"]
        items: list[tuple[str, Callable[[], None]]] = []
        if dest != "pool":
            current = layout._list(dest)
            i = current.index(mid)
            if i > 0:
                items.append(("Move up", lambda: drop(mid, dest, i - 1)))
            if i < len(current) - 1:
                items.append(("Move down", lambda: drop(mid, dest, i + 2)))
        targets = [("top", "Move to the top row")]
        targets += [(("col", n), f"Move to column {n + 1}") for n in range(len(layout.columns))]
        targets += [("drawer", "Move to the drawer"), ("tools", "Move to the tools")]
        for target, text in targets:
            if target != dest and allowed(area, target):
                items.append((text, lambda t=target: drop(mid, t, None)))
        if dest != "pool":
            items.append(("Remove from the panel", lambda: drop(mid, "pool", None)))
        _menu(anchor, event, items, ctx)

    def section(dest, title: str, ids: list[str], empty: str, *extra: Gtk.Widget) -> None:
        layout: Layout = state["layout"]
        rows = []
        for mid in ids:
            meta = info(mid)
            holder: dict = {}
            row = _row(meta["title"], meta["description"], meta["icon"], layout.visible(mid),
                       lambda on, m=mid: switched(m, on), mid,
                       lambda event, m=mid, h=holder: menu_for(m, dest, event, h["row"]))
            holder["row"] = row
            rows.append(row)
        outer.pack_start(_heading(title, *extra), False, False, 0)
        outer.pack_start(_zone(rows, lambda mid, index, d=dest: drop(mid, d, index), empty), False, False, 0)

    def render() -> None:
        for child in outer.get_children():
            outer.remove(child)
            child.destroy()
        layout: Layout = state["layout"]
        shown = set(layout.shown_ids())
        if "header" not in shown and "commandbar" not in shown:
            warn = label("With both the clock and the command bar off, open settings with "
                         "`cornerwindow settings`.", "cw-small cw-warn")
            warn.set_line_wrap(True)
            outer.pack_start(warn, False, False, 0)
        presets = Gtk.Box(spacing=6)
        presets.pack_start(label("Start from", "cw-dim cw-small"), False, False, 0)
        for name, title, tip in (("essentials", "Essentials", "Display, sound, now playing, the clock, toggles "
                                  "and the drawer; cards show up only while they have something to show"),
                                 ("everything", "Everything", "Every card and tool")):
            presets.pack_start(chip(title, lambda n=name: later(apply_preset, n), tip), False, False, 0)
        outer.pack_start(presets, False, False, 0)
        section("top", "Top row", layout.top, "Drop a card here")
        for n, column in enumerate(layout.columns):
            extra = []
            if len(layout.columns) > 1:
                extra.append(IconButton("clear", lambda i=n: later(layout.remove_column, i, commit),
                                        "Remove column (its cards move left)", "cw-icon cw-plain", 12))
            section(("col", n), f"Column {n + 1}", column, "Drop a card here", *extra)
        tools = Gtk.Box(spacing=6)
        tools.pack_start(chip("+ Column", lambda: later(layout.add_column, render)), False, False, 0)
        outer.pack_start(tools, False, False, 0)
        section("drawer", "Drawer tabs", layout.drawer, "Drop a drawer tab here")
        section("tools", "Tools", layout.tools, "Drop a tool here")
        pool = layout.unplaced(sorted(catalog(), key=lambda m: info(m)["title"].lower()))
        section("pool", "Not in the panel", pool, "Every card is placed")
        outer.show_all()

    def apply_preset(name: str) -> None:
        state["layout"] = preset(name)
        commit()

    def _set(v) -> None:
        state["layout"] = Layout.from_config(v or {})
        render()

    editor._set = _set
    return editor


# ── reorderable switch list (toggles, power buttons) ───────────────────────
def switchlist_editor(field, value, on_change, ctx):
    from .editors import Editor, _emit

    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    editor = Editor(outer)
    source = ctx.get("choices")
    choices = source(ctx.get("section", ""), ctx.get("key", "")) if callable(source) else []
    meta = {cid: (title, desc) for cid, title, desc in choices}
    state: dict[str, Any] = {"order": [], "on": set()}

    def commit() -> None:
        _emit(editor, on_change, [i for i in state["order"] if i in state["on"]])
        render()

    def switched(item: str, on: bool) -> None:
        if (item in state["on"]) == on:
            return
        (state["on"].add if on else state["on"].discard)(item)
        GLib.idle_add(lambda: commit() and False)

    def drop(item: str, index: int | None) -> None:
        order = state["order"]
        if item not in order:
            return
        old = order.index(item)
        order.remove(item)
        if index is None:
            index = len(order)
        elif old < index:
            index -= 1
        order.insert(max(0, min(index, len(order))), item)
        commit()

    def menu_for(item: str, event, anchor) -> None:
        i = state["order"].index(item)
        items = []
        if i > 0:
            items.append(("Move up", lambda: drop(item, i - 1)))
        if i < len(state["order"]) - 1:
            items.append(("Move down", lambda: drop(item, i + 2)))
        if items:
            _menu(anchor, event, items, ctx)

    def render() -> None:
        for child in outer.get_children():
            outer.remove(child)
            child.destroy()
        rows = []
        for item in state["order"]:
            title, desc = meta.get(item, (item, "Unknown here"))
            holder: dict = {}
            row = _row(title, desc, None, item in state["on"], lambda on, i=item: switched(i, on), item,
                       lambda event, i=item, h=holder: menu_for(i, event, h["row"]))
            holder["row"] = row
            rows.append(row)
        outer.pack_start(_zone(rows, drop, "Nothing to show"), False, False, 0)
        outer.show_all()

    def _set(v) -> None:
        items = [str(i) for i in (v or [])]
        state["on"] = set(items)
        state["order"] = items + [cid for cid in meta if cid not in items]
        render()

    editor._set = _set
    return editor
