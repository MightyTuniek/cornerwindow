"""One editor widget per field kind. Each returns an Editor with .widget and .set(value);
user edits call on_change(value) with a plain Python value ready to write to TOML."""

from __future__ import annotations

import tomllib
from typing import Any, Callable

from gi.repository import Gdk, Gtk

from ...config_edit import dump_value
from .. import theme
from ..widgets import IconButton, chip, label
from .schema import Field

OnChange = Callable[[Any], None]


class Editor:
    def __init__(self, widget: Gtk.Widget) -> None:
        self.widget = widget
        self.quiet = False

    def set(self, value: Any) -> None:  # programmatic update, must not call on_change
        self.quiet = True
        try:
            self._set(value)
        finally:
            self.quiet = False

    def _set(self, value: Any) -> None:
        pass


def make(field: Field, value: Any, on_change: OnChange, ctx: dict) -> Editor:
    from .layout_editor import layout_editor, switchlist_editor

    builder = {
        "layout": layout_editor, "switchlist": switchlist_editor, "place_search": _place_search,
        "bool": _bool, "int": _number, "float": _number, "choice": _choice, "action": _choice,
        "sensor": _choice, "text": _text, "color": _color, "list": _list, "modules": _list,
        "optional_int": _optional, "optional_float": _optional, "columns": _columns,
        "presets": _presets, "dict": _dict, "limits": _limits, "toml": _toml,
    }.get(field.kind, _toml)
    editor = builder(field, value, on_change, ctx)
    editor.set(value)
    return editor


def _emit(editor: Editor, on_change: OnChange, value: Any) -> None:
    if not editor.quiet:
        on_change(value)


# ── scalars ──────────────────────────────────────────────────────────────────
def _bool(field, value, on_change, ctx) -> Editor:
    switch = Gtk.Switch()
    switch.set_valign(Gtk.Align.CENTER)
    editor = Editor(switch)
    editor._set = lambda v: switch.set_active(bool(v))
    switch.connect("notify::active", lambda s, _p: _emit(editor, on_change, s.get_active()))
    return editor


def _compact(low: float, high: float, value: float, on_value: Callable[[int], None]) -> Gtk.Entry:
    """A small number field for tables: type a number, or scroll / press ↑↓ over it."""
    entry = Gtk.Entry()
    entry.set_width_chars(3)
    entry.set_max_width_chars(3)
    entry.set_alignment(0.5)
    entry.set_valign(Gtk.Align.CENTER)
    entry.get_style_context().add_class("cw-compact")
    entry.set_tooltip_text(f"{int(low)}–{int(high)} · scroll or ↑↓ to adjust")
    entry.add_events(Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
    state = {"value": int(value)}
    entry.set_text(str(state["value"]))

    def apply(number: float) -> None:
        number = int(max(low, min(high, round(number))))
        entry.set_text(str(number))
        if number != state["value"]:
            state["value"] = number
            on_value(number)

    def commit(*_a):
        try:
            apply(float(entry.get_text().strip() or 0))
        except ValueError:
            entry.set_text(str(state["value"]))
        return False

    def scroll(_e, event):
        if event.direction == Gdk.ScrollDirection.SMOOTH:
            _ok, _dx, dy = event.get_scroll_deltas()
            delta = -1 if dy > 0 else 1 if dy < 0 else 0
        else:
            delta = 1 if event.direction == Gdk.ScrollDirection.UP else -1
        apply(state["value"] + delta)
        return True

    def key(_e, event):
        if event.keyval in (Gdk.KEY_Up, Gdk.KEY_Down):
            apply(state["value"] + (1 if event.keyval == Gdk.KEY_Up else -1))
            return True
        return False

    def set_number(number: float) -> None:
        state["value"] = int(number)
        entry.set_text(str(int(number)))

    entry.set_number = set_number  # programmatic update without emitting
    entry.connect("activate", commit)
    entry.connect("focus-out-event", commit)
    entry.connect("scroll-event", scroll)
    entry.connect("key-press-event", key)
    return entry


def _spin(field: Field, low=None, high=None) -> Gtk.SpinButton:
    spin = Gtk.SpinButton.new_with_range(field.min if low is None else low,
                                         field.max if high is None else high, field.step or 1)
    spin.set_digits(field.digits)
    spin.set_numeric(True)
    spin.set_width_chars(4)
    spin.set_valign(Gtk.Align.CENTER)
    return spin


def _number(field, value, on_change, ctx) -> Editor:
    box = Gtk.Box(spacing=6)
    spin = _spin(field)
    box.pack_start(spin, False, False, 0)
    if field.unit:
        box.pack_start(label(field.unit, "cw-dim cw-small"), False, False, 0)
    editor = Editor(box)

    def _set(v):
        try:
            spin.set_value(float(v))
        except (TypeError, ValueError):
            pass

    def changed(s):
        number = s.get_value()
        _emit(editor, on_change, int(round(number)) if field.kind == "int" else round(number, max(field.digits, 2)))

    editor._set = _set
    spin.connect("value-changed", changed)
    return editor


def _choice(field, value, on_change, ctx) -> Editor:
    combo = Gtk.ComboBoxText()
    combo.set_valign(Gtk.Align.CENTER)
    if field.kind == "action":
        options = ["none"] + ctx.get("actions", [])
    elif field.kind == "sensor":
        options = ["auto"] + ctx.get("sensors", [])
    else:
        options = list(field.choices)
    editor = Editor(combo)
    known: set[str] = set()

    def add(option: str) -> None:
        if option not in known:
            known.add(option)
            combo.append(option, option)

    for option in options:
        add(option)

    def _set(v):
        text = ", ".join(map(str, v)) if isinstance(v, list) else str(v)
        add(text)
        combo.set_active_id(text)

    editor._set = _set
    combo.connect("changed", lambda c: _emit(editor, on_change, c.get_active_id()))
    theme_popup(combo, ctx)  # before the popup is first realized, so its RGBA visual sticks
    combo.connect("notify::popup-shown", lambda c, _p: c.get_property("popup-shown") and theme_popup(c, ctx))
    return editor


def theme_popup(widget: Gtk.Widget, ctx: dict) -> None:
    """Give a widget's popup menus (a combo box's list) the panel's look.

    The menu lives in its own toplevel outside `.cw-root`, so it gets `cw-menu` plus the
    current theme classes, and the black theme's configurable background colour."""
    palette = theme.current()
    for menu in Gtk.Menu.get_for_attach_widget(widget):
        style = menu.get_style_context()
        for cls in ("cw-menu", "cw-glass", "cw-light"):
            (style.add_class if cls == "cw-menu" or cls in palette.css_classes else style.remove_class)(cls)
        top = menu.get_toplevel()
        if top is not menu:
            top.get_style_context().add_class("cw-popup")
            screen = top.get_screen()
            visual = screen.get_rgba_visual()
            if visual is not None and screen.is_composited() and top.get_visual() != visual \
                    and not top.get_realized():
                top.set_visual(visual)
        # Refilled on every call: the theme can change between popups, and a stale
        # black background would sit under glass-light's dark text.
        provider = getattr(menu, "_cw_bg", None)
        if provider is None:
            provider = menu._cw_bg = Gtk.CssProvider()
            style.add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        bg = ctx.get("background")
        css = ""
        if not palette.glass and bg is not None:
            css = f"menu.cw-menu {{ background-color: {bg().to_string()}; }}"
        provider.load_from_data(css.encode())


def _entry(on_commit: Callable[[str], None], width: int = 16) -> Gtk.Entry:
    entry = Gtk.Entry()
    entry.set_width_chars(width)
    entry.set_valign(Gtk.Align.CENTER)
    entry.connect("activate", lambda e: on_commit(e.get_text()))
    entry.connect("focus-out-event", lambda e, _ev: on_commit(e.get_text()) or False)
    return entry


def _text(field, value, on_change, ctx) -> Editor:
    holder: dict = {}

    def commit(text: str) -> None:
        if text != holder.get("last"):
            holder["last"] = text
            if field.key == "monitor" and text.strip().isdigit():
                _emit(editor, on_change, int(text))
            else:
                _emit(editor, on_change, text)

    entry = _entry(commit)
    editor = Editor(entry)

    def _set(v):
        holder["last"] = "" if v is None else str(v)
        entry.set_text(holder["last"])

    editor._set = _set
    return editor


def _optional(field, value, on_change, ctx) -> Editor:
    holder: dict = {}

    def commit(text: str) -> None:
        text = text.strip()
        if text == holder.get("last"):
            return
        holder["last"] = text
        if not text:
            _emit(editor, on_change, None)  # None = remove from your file
            return
        try:
            number = int(text) if field.kind == "optional_int" else float(text.replace(",", "."))
        except ValueError:
            entry.get_style_context().add_class("cw-error")
            return
        entry.get_style_context().remove_class("cw-error")
        _emit(editor, on_change, number)

    entry = _entry(commit, 10)
    entry.set_placeholder_text("auto")
    editor = Editor(entry)

    def _set(v):
        holder["last"] = "" if v is None else str(v)
        entry.set_text(holder["last"])

    editor._set = _set
    return editor


def _color(field, value, on_change, ctx) -> Editor:
    button = Gtk.ColorButton()
    button.set_use_alpha(False)
    button.set_valign(Gtk.Align.CENTER)
    editor = Editor(button)

    def _set(v):
        rgba = Gdk.RGBA()
        if rgba.parse(str(v)):
            button.set_rgba(rgba)

    def picked(b):
        c = b.get_rgba()
        _emit(editor, on_change, "#{:02x}{:02x}{:02x}".format(*(round(x * 255) for x in (c.red, c.green, c.blue))))

    editor._set = _set
    button.connect("color-set", picked)
    return editor


def _parse_list(text: str, item_type: type) -> list:
    items = [part.strip() for part in text.replace("\n", ",").split(",") if part.strip()]
    if item_type is int:
        return [int(float(i)) for i in items]
    if item_type is float:
        return [float(i) for i in items]
    return items


def _list(field, value, on_change, ctx) -> Editor:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
    holder: dict = {}

    def commit(text: str) -> None:
        try:
            items = _parse_list(text, field.item_type)
        except ValueError:
            entry.get_style_context().add_class("cw-error")
            return
        entry.get_style_context().remove_class("cw-error")
        if items != holder.get("last"):
            holder["last"] = items
            # a single-entry backend list stays a plain string ("auto")
            if field.key == "backend" and len(items) == 1:
                _emit(editor, on_change, items[0])
            else:
                _emit(editor, on_change, items)

    entry = _entry(commit, 12)
    entry.set_hexpand(True)
    box.pack_start(entry, False, False, 0)
    if field.kind == "modules":
        box.pack_start(_hint("Available: " + ", ".join(ctx.get("modules", []))), False, False, 0)
    editor = Editor(box)

    def _set(v):
        items = v if isinstance(v, list) else ([] if v is None else [v])
        holder["last"] = items
        entry.set_text(", ".join(map(str, items)))

    editor._set = _set
    return editor


def _hint(text: str) -> Gtk.Label:
    hint = label(text, "cw-dim cw-small")
    hint.set_line_wrap(True)
    hint.set_max_width_chars(30)
    return hint


# ── structured values ────────────────────────────────────────────────────────
def _columns(field, value, on_change, ctx) -> Editor:
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    outer.pack_start(rows, False, False, 0)
    state: dict = {"columns": []}
    editor = Editor(outer)

    def emit() -> None:
        _emit(editor, on_change, [list(c) for c in state["columns"] if c] or [[]])

    def render() -> None:
        for child in rows.get_children():
            rows.remove(child)
        for index, column in enumerate(state["columns"]):
            row = Gtk.Box(spacing=6)
            row.pack_start(label(f"{index + 1}", "cw-dim cw-small"), False, False, 0)

            def commit(text, i=index):
                try:
                    items = _parse_list(text, str)
                except ValueError:
                    return
                if items != state["columns"][i]:
                    state["columns"][i] = items
                    emit()

            entry = _entry(commit, 12)
            entry.set_hexpand(True)
            entry.set_text(", ".join(column))
            row.pack_start(entry, True, True, 0)

            def remove(i=index):
                del state["columns"][i]
                render()
                emit()

            if len(state["columns"]) > 1:
                row.pack_start(IconButton("clear", remove, "Remove column", "cw-icon cw-plain", 14), False, False, 0)
            rows.pack_start(row, False, False, 0)
        rows.show_all()

    def add_column():
        state["columns"].append([])
        render()

    tools = Gtk.Box(spacing=6)
    add_button = chip("+ Column", add_column)
    tools.pack_start(add_button, False, False, 0)
    outer.pack_start(tools, False, False, 0)
    outer.pack_start(_hint("Module ids, comma separated. Available: " + ", ".join(ctx.get("modules", []))),
                     False, False, 0)

    def _set(v):
        state["columns"] = [list(c) for c in (v or [])]
        render()

    editor._set = _set
    return editor


def _presets(field, value, on_change, ctx) -> Editor:
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    grid = Gtk.Grid(column_spacing=6, row_spacing=6)
    outer.pack_start(grid, False, False, 0)
    state: dict = {"items": []}
    editor = Editor(outer)
    limits = ctx.get("limits", {})

    def emit() -> None:
        _emit(editor, on_change, [dict(p) for p in state["items"]])

    def render() -> None:
        for child in grid.get_children():
            grid.remove(child)
        for col, title in enumerate(("Name", "Bright.", "Contr.", "Night")):
            grid.attach(label(title, "cw-title"), col, 0, 1, 1)
        for row, preset in enumerate(state["items"], start=1):
            def set_key(key, val, i=row - 1):
                if state["items"][i].get(key) != val:
                    state["items"][i][key] = val
                    emit()

            name = _entry(lambda t, f=set_key: f("name", t.strip() or "Preset"), 7)
            name.set_text(str(preset.get("name", "")))
            name.set_hexpand(True)
            grid.attach(name, 0, row, 1, 1)
            for col, key in ((1, "brightness"), (2, "contrast")):
                number = _compact(0, float(limits.get(key, 100)), float(preset.get(key, 0)),
                                  lambda v, k=key, f=set_key: f(k, v))
                grid.attach(number, col, row, 1, 1)
            switch = Gtk.Switch()
            switch.set_valign(Gtk.Align.CENTER)
            switch.set_active(bool(preset.get("night_light", False)))
            switch.connect("notify::active", lambda s, _p, f=set_key: f("night_light", s.get_active()))
            grid.attach(switch, 3, row, 1, 1)

            def remove(i=row - 1):
                del state["items"][i]
                render()
                emit()

            grid.attach(IconButton("clear", remove, "Remove preset", "cw-icon cw-plain", 14), 4, row, 1, 1)
        grid.show_all()

    def add():
        state["items"].append({"name": f"Preset {len(state['items']) + 1}", "brightness": 0, "contrast": 0,
                               "night_light": False})
        render()
        emit()

    add_button = chip("+ Preset", add)
    add_button.set_halign(Gtk.Align.START)
    outer.pack_start(add_button, False, False, 0)

    def _set(v):
        state["items"] = [dict(p) for p in (v or [])]
        render()

    editor._set = _set
    return editor


def _limits(field, value, on_change, ctx) -> Editor:
    grid = Gtk.Grid(column_spacing=8, row_spacing=6)
    state: dict = {"limits": {}}
    editor = Editor(grid)
    spins = {}
    for row, key in enumerate(("brightness", "contrast")):
        grid.attach(label(key.capitalize(), "cw-small"), 0, row, 1, 1)

        def changed(value, k=key):
            limits = dict(state["limits"])
            if value >= 100:
                limits.pop(k, None)  # 100 = no limit
            else:
                limits[k] = value
            if limits != state["limits"]:
                state["limits"] = limits
                _emit(editor, on_change, limits)

        number = _compact(1, 100, 100, changed)
        grid.attach(number, 1, row, 1, 1)
        grid.attach(label("%", "cw-dim cw-small"), 2, row, 1, 1)
        spins[key] = number
    grid.attach(label("100 = no limit", "cw-dim cw-small"), 0, 2, 3, 1)

    def _set(v):
        state["limits"] = dict(v or {})
        for key, number in spins.items():
            number.set_number(state["limits"].get(key, 100))

    editor._set = _set
    return editor


def _dict(field, value, on_change, ctx) -> Editor:
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    outer.pack_start(rows, False, False, 0)
    state: dict = {"pairs": []}
    editor = Editor(outer)

    def emit() -> None:
        _emit(editor, on_change, {k: v for k, v in state["pairs"] if k})

    def coerce(text: str, like: Any) -> Any:
        if isinstance(like, bool):
            return text.strip().lower() in ("1", "true", "yes", "on")
        for cast in ((int,) if isinstance(like, int) else ()) + ((float,) if isinstance(like, float) else ()):
            try:
                return cast(text)
            except ValueError:
                pass
        return text

    def render() -> None:
        for child in rows.get_children():
            rows.remove(child)
        for index, (key, val) in enumerate(state["pairs"]):
            row = Gtk.Box(spacing=6)

            def set_key(text, i=index):
                k, v = state["pairs"][i]
                if text != k:
                    state["pairs"][i] = (text.strip(), v)
                    emit()

            def set_val(text, i=index):
                k, v = state["pairs"][i]
                new = coerce(text, v)
                if new != v:
                    state["pairs"][i] = (k, new)
                    emit()

            k_entry = _entry(set_key, 10)
            k_entry.set_text(str(key))
            v_entry = _entry(set_val, 10)
            v_entry.set_text(str(val))
            row.pack_start(k_entry, True, True, 0)
            row.pack_start(label("→", "cw-dim"), False, False, 0)
            row.pack_start(v_entry, True, True, 0)

            def remove(i=index):
                del state["pairs"][i]
                render()
                emit()

            row.pack_start(IconButton("clear", remove, "Remove", "cw-icon cw-plain", 14), False, False, 0)
            rows.pack_start(row, False, False, 0)
        rows.show_all()

    def add():
        state["pairs"].append(("", ""))
        render()

    add_button = chip("+ Add", add)
    add_button.set_halign(Gtk.Align.START)
    outer.pack_start(add_button, False, False, 0)

    def _set(v):
        state["pairs"] = list((v or {}).items())
        render()

    editor._set = _set
    return editor


def _toml(field, value, on_change, ctx) -> Editor:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    view = Gtk.TextView()
    view.set_monospace(True)
    view.get_style_context().add_class("cw-notes")
    view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    box.pack_start(view, False, False, 0)
    status = label("", "cw-dim cw-small")
    box.pack_start(status, False, False, 0)
    editor = Editor(box)

    def commit(*_a):
        buf = view.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        try:
            parsed = tomllib.loads(f"v = {text}")["v"]
        except tomllib.TOMLDecodeError as exc:
            status.set_text(f"Not valid TOML: {exc}")
            return
        status.set_text("")
        _emit(editor, on_change, parsed)

    view.connect("focus-out-event", lambda *_a: commit() or False)

    def _set(v):
        view.get_buffer().set_text(dump_value(v) if v is not None else "")

    editor._set = _set
    return editor


def _place_search(field, value, on_change, ctx) -> Editor:
    """Type a city, press Find, pick a match: writes place, latitude and longitude together.
    The name goes to Open-Meteo's geocoder only when Find is pressed."""
    from ...core import tasks
    from ...modules.weather import geocode

    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    line = Gtk.Box(spacing=6)
    entry = Gtk.Entry()
    entry.set_placeholder_text("City, e.g. Warsaw")
    entry.set_hexpand(True)
    line.pack_start(entry, True, True, 0)
    find = chip("Find")
    line.pack_start(find, False, False, 0)
    outer.pack_start(line, False, False, 0)
    current = label("", "cw-dim cw-small")
    outer.pack_start(current, False, False, 0)
    results = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    outer.pack_start(results, False, False, 0)
    editor = Editor(outer)

    def clear_results() -> None:
        for child in results.get_children():
            results.remove(child)
            child.destroy()

    def show(matches) -> None:
        clear_results()
        if not matches:
            results.pack_start(label("No place found", "cw-dim cw-small"), False, False, 0)
        for match in matches:
            button = Gtk.Button(label=match["label"])
            button.set_relief(Gtk.ReliefStyle.NONE)
            button.set_can_focus(False)
            button.get_style_context().add_class("cw-row")
            button.get_child().set_xalign(0)

            def choose(_b, m=match) -> None:
                entry.set_text(m["place"])
                clear_results()
                _emit(editor, on_change, {"place": m["place"], "latitude": m["latitude"],
                                          "longitude": m["longitude"]})

            button.connect("clicked", choose)
            results.pack_start(button, False, False, 0)
        results.show_all()

    def search(*_a) -> None:
        name = entry.get_text().strip()
        if not name:
            return
        clear_results()
        results.pack_start(label("Searching…", "cw-dim cw-small"), False, False, 0)
        results.show_all()
        tasks.run_async(lambda: geocode(name), show,
                        error=lambda exc: show([]) or current.set_text(f"Search failed: {exc}"))

    find.connect("clicked", search)
    entry.connect("activate", search)

    def _set(v) -> None:
        v = v or {}
        entry.set_text(str(v.get("place") or ""))
        lat, lon = v.get("latitude"), v.get("longitude")
        current.set_text(f"At {lat}, {lon}" if lat is not None and lon is not None else "No place set yet")

    editor._set = _set
    return editor
