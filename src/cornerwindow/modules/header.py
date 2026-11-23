"""Clock, date (click it for a month calendar), pin and settings."""

from __future__ import annotations

import datetime as dt
import time

from gi.repository import Gdk, Gtk

from .. import config as config_mod
from ..core.dates import first_weekday, month_grid, shift_month, weekday_letters
from ..platform.desktop import open_uri
from ..ui.widgets import Card, HoldButton, IconButton, label
from ..ui.widgets.card import set_active
from .base import Command, Module, module


CELL = 24


def _centered(cr, text: str, cx: float, cy: float, rgba) -> None:
    ext = cr.text_extents(text)
    cr.move_to(cx - ext.x_advance / 2, cy - ext.y_bearing - ext.height / 2)
    cr.set_source_rgba(*rgba)
    cr.show_text(text)


@module("header", title="Clock", icon="clock",
        description="Clock, date, your next event and a month calendar; pin, settings and power buttons")
class Header(Module):
    def setup(self) -> None:
        self.events_on = bool(self.ctx.config.get("calendar.enabled", True))
        self.calendar_svc = self.ctx.services.calendar if self.events_on else None
        self._event_days: dict[tuple[int, int], set] = {}
        if self.calendar_svc is not None:
            self.ctx.bus.subscribe("calendar.changed", self._events_changed)
            self.calendar_svc.refresh()  # once at start (off the main thread): ready at the first open

    def configure(self) -> None:
        if self.calendar_svc is not None:
            self.calendar_svc.reconfigure(self.ctx.config.section("calendar"))

    def build(self):
        card = Card(css="cw-flat", orientation=Gtk.Orientation.HORIZONTAL)
        right_corner = self.ctx.island.corner.endswith("right")
        # keep the very corner free: clicking it closes the island
        if right_corner:
            card.set_margin_end(26)
        else:
            card.set_margin_start(26)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.clock = label("", "cw-clock")
        self.date = label("", "cw-date")
        texts.pack_start(self.clock, False, False, 0)
        # the date and the next event share one click target: either opens the calendar
        date_box = Gtk.Button()
        date_box.set_relief(Gtk.ReliefStyle.NONE)
        date_box.set_can_focus(False)
        date_box.get_style_context().add_class("cw-bare")
        dates = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        dates.pack_start(self.date, False, False, 0)
        self.next_event = label("", "cw-event", ellipsize=True)
        self.next_event.set_no_show_all(True)
        dates.pack_start(self.next_event, False, False, 0)
        date_box.add(dates)
        date_box.set_tooltip_text("Calendar")
        date_box.connect("clicked", lambda *_a: self.toggle_calendar())
        texts.pack_start(date_box, False, False, 0)
        buttons = Gtk.Box(spacing=6)
        buttons.set_valign(Gtk.Align.CENTER)
        self.pin = IconButton("pin", self.toggle_pin, "Keep open")
        buttons.pack_start(self.pin, False, False, 0)
        buttons.pack_start(IconButton("settings", self.open_settings, "Settings"), False, False, 0)
        self.power_button = None
        if self.config.get("power", True) and not self._power_card_shown():
            self.power_button = IconButton("poweroff", self.toggle_power, "Lock, sleep, restart, shut down")
            buttons.pack_start(self.power_button, False, False, 0)
        if right_corner:
            card.pack_start(texts, True, True, 0)
            card.pack_end(buttons, False, False, 0)
        else:
            card.pack_start(buttons, False, False, 0)
            card.pack_end(texts, True, True, 0)
            self.clock.set_xalign(1.0)
            self.date.set_xalign(1.0)
            self.next_event.set_xalign(1.0)
        self.ctx.every(1.0, self.tick)
        self.tick()
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.pack_start(card, False, False, 0)
        outer.pack_start(self._build_power(), False, False, 0)
        outer.pack_start(self._build_calendar(), False, False, 0)
        return outer

    # -- power ------------------------------------------------------------------------
    def _power_card_shown(self) -> bool:
        cfg = self.ctx.config
        placed = list(cfg.get("modules.top", []) or [])
        for column in cfg.get("modules.columns", []) or []:
            placed += list(column)
        return "power" in placed and "power" not in (cfg.get("modules.hidden", []) or [])

    def _build_power(self) -> Gtk.Widget:
        """The power card's buttons, folded into the clock: one button instead of a whole card."""
        from .power import ACTIONS, run_action

        self.power_row = Gtk.Revealer()
        self.power_row.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.power_row.set_transition_duration(220)
        if self.power_button is None:
            return self.power_row
        section = self.ctx.config.section("power")
        items = [i for i in section.get("items", ["lock", "suspend", "reboot", "poweroff"]) if i in ACTIONS]
        hold = int(section.get("hold_ms", 600))
        box = Card()
        row = Gtk.Box(spacing=0, homogeneous=True)
        for key in items:
            icon, title, _fn, color = ACTIONS[key]
            ms = int(section.get("lock_hold_ms", 250)) if key == "lock" else hold
            row.pack_start(HoldButton(icon, title, lambda k=key: run_action(self.ctx, k), ms, color),
                           True, True, 0)
        box.pack_start(row, False, False, 0)
        box.set_margin_top(8)
        self.power_row.add(box)
        return self.power_row

    def toggle_power(self) -> None:
        show = not self.power_row.get_reveal_child()
        self.power_row.set_reveal_child(show)
        set_active(self.power_button, show)

    # -- calendar ---------------------------------------------------------------------
    def _build_calendar(self) -> Gtk.Widget:
        today = dt.date.today()
        self.shown_month = (today.year, today.month)
        self.calendar = Gtk.Revealer()
        self.calendar.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.calendar.set_transition_duration(240)
        box = Card(spacing=4)
        head = Gtk.Box(spacing=4)
        self.month_label = label("", "cw-strong")
        head.pack_start(self.month_label, True, True, 0)
        head.pack_start(IconButton("chevron-left", lambda: self._shift(-1), "Previous month",
                                   "cw-icon cw-plain", 12), False, False, 0)
        head.pack_start(IconButton("calendar", self._today, "Today", "cw-icon cw-plain", 12), False, False, 0)
        head.pack_start(IconButton("chevron-right", lambda: self._shift(1), "Next month",
                                   "cw-icon cw-plain", 12), False, False, 0)
        box.pack_start(head, False, False, 0)
        self.grid = Gtk.DrawingArea()
        self.grid.set_size_request(-1, 7 * CELL)
        self.grid.connect("draw", self._draw_grid)
        self.grid.add_events(Gdk.EventMask.SCROLL_MASK)
        self.grid.connect("scroll-event", self._scroll)
        box.pack_start(self.grid, False, False, 0)
        self.agenda = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.pack_start(self.agenda, False, False, 0)
        self.calendar.add(box)
        self.calendar.set_reveal_child(bool(self.ctx.state.get("header.calendar", False)))
        self._update_month()
        return self.calendar

    def toggle_calendar(self) -> bool:
        show = not self.calendar.get_reveal_child()
        if show:
            self._today()
        self.calendar.set_reveal_child(show)
        self.ctx.state.set("header.calendar", show)
        return True

    def _shift(self, delta: int) -> None:
        self.shown_month = shift_month(*self.shown_month, delta)
        self._update_month()

    def _today(self) -> None:
        today = dt.date.today()
        self.shown_month = (today.year, today.month)
        self._update_month()

    def _scroll(self, _w, event) -> bool:
        if event.direction == Gdk.ScrollDirection.UP:
            self._shift(-1)
        elif event.direction == Gdk.ScrollDirection.DOWN:
            self._shift(1)
        elif event.direction == Gdk.ScrollDirection.SMOOTH:
            _ok, _dx, dy = event.get_scroll_deltas()
            if abs(dy) >= 1:
                self._shift(1 if dy > 0 else -1)
        return True

    def _update_month(self) -> None:
        year, month = self.shown_month
        self.month_label.set_text(dt.date(year, month, 1).strftime("%B %Y"))
        self.grid.queue_draw()

    # -- events -----------------------------------------------------------------------
    def _events_changed(self, **_kw) -> None:
        self._event_days.clear()
        if hasattr(self, "next_event"):
            self._show_events()
            self.grid.queue_draw()

    def _days_with_events(self, year: int, month: int) -> set:
        if self.calendar_svc is None:
            return set()
        key = (year, month)
        if key not in self._event_days:
            self._event_days[key] = self.calendar_svc.days_with_events(year, month)
        return self._event_days[key]

    def _show_events(self) -> None:
        if self.calendar_svc is None:
            return
        from ..platform.calendar import format_next

        now = dt.datetime.now().astimezone()
        upcoming = self.calendar_svc.upcoming(int(self.config.get("agenda", 4)) or 1,
                                              int(self.ctx.config.get("calendar.days", 7)))
        if upcoming and self.config.get("next_event", True):
            self.next_event.set_text(format_next(upcoming[0], now))
            self.next_event.show()
        else:
            self.next_event.hide()
        for child in self.agenda.get_children():
            self.agenda.remove(child)
            child.destroy()
        for occ in upcoming:
            line = Gtk.Box(spacing=8)
            when = "All day" if occ.all_day else occ.start.astimezone(now.tzinfo).strftime("%H:%M")
            if occ.start.astimezone(now.tzinfo).date() != now.date():
                when = occ.start.astimezone(now.tzinfo).strftime("%a ") + ("" if occ.all_day else when)
            line.pack_start(label(when.strip(), "cw-dim cw-small cw-tnum"), False, False, 0)
            line.pack_start(label(occ.summary, "cw-small", ellipsize=True), True, True, 0)
            self.agenda.pack_start(line, False, False, 0)
        self.agenda.show_all()

    def _draw_grid(self, widget, cr) -> bool:
        from ..ui import theme

        p = theme.current()
        week_start = first_weekday(self.config.get("week_start", "monday"))
        year, month = self.shown_month
        width = widget.get_allocated_width()
        cell_w = width / 7
        today = dt.date.today()
        cr.select_font_face("Sans")
        cr.set_font_size(10.5)
        for i, letters in enumerate(weekday_letters(week_start)):
            _centered(cr, letters, (i + 0.5) * cell_w, CELL / 2, p.text_dim)
        cr.set_font_size(12)
        busy_days = self._days_with_events(year, month)
        for row, week in enumerate(month_grid(year, month, week_start)):
            for col, day in enumerate(week):
                cx, cy = (col + 0.5) * cell_w, (row + 1.5) * CELL
                if day in busy_days and day != today:
                    cr.arc(cx, cy + CELL / 2 - 3, 1.6, 0, 6.283185307)
                    cr.set_source_rgba(*p.text_dim)
                    cr.fill()
                if day == today:
                    cr.arc(cx, cy, CELL / 2 - 2, 0, 6.283185307)
                    cr.set_source_rgba(0.039, 0.518, 1.0, 1.0)
                    cr.fill()
                    color = (1, 1, 1, 1)
                else:
                    color = p.text if day.month == month else p.text_dim[:3] + (p.text_dim[3] * 0.6,)
                _centered(cr, str(day.day), cx, cy, color)
        return True

    def on_open(self) -> None:
        if self.calendar_svc is not None:
            self.calendar_svc.refresh()
            self._show_events()

    def tick(self) -> None:
        now = time.localtime()
        if self.calendar_svc is not None and now.tm_sec == 0 and hasattr(self, "next_event"):
            self._show_events()  # "in 5 min" -> "in 4 min"
        self.clock.set_text(time.strftime(self.config.get("clock_format", "%H:%M"), now))
        self.date.set_text(time.strftime(self.config.get("date_format", "%A, %-d %B"), now))

    def toggle_pin(self) -> None:
        pinned = not self.ctx.island.pinned
        self.ctx.island.set_pinned(pinned)
        set_active(self.pin, pinned)

    def on_close(self) -> None:
        set_active(self.pin, False)
        if self.power_button is not None:
            self.power_row.set_reveal_child(False)
            set_active(self.power_button, False)

    def open_settings(self) -> None:
        if self.ctx.actions.has("settings.toggle"):
            self.ctx.actions.run("settings.toggle")
        else:  # running without the app (tests): fall back to the file
            open_uri(config_mod.ensure_user_file().as_uri())

    def commands(self) -> list[Command]:
        if self._power_card_shown():
            return []  # the Power card lists them
        from .power import power_commands

        return power_commands(self.ctx)
