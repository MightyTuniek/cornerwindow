"""AI agents: Claude Code and Codex usage today, the current 5-hour window and when it resets,
plan limits where the tool logs them, and which projects have an agent working right now.
Read from their local logs only. Hidden when neither tool is installed; off by default."""

from __future__ import annotations

import datetime as dt

from gi.repository import Gtk

from ..core.activity import PURPLE
from ..platform.agents import AgentUsage, human_tokens
from ..ui.draw import pill
from ..ui.widgets import Card, label
from .base import Command, Module, module


@module("agents", title="AI agents", icon="agent",
        description="Claude Code and Codex: tokens today, the 5-hour window, limits, who's working")
class Agents(Module):
    def setup(self) -> None:
        self.usage = AgentUsage()
        self.latest = []

    def available(self) -> bool:
        return self.usage.available()

    def build(self):
        self.card = Card("AI agents", spacing=8)
        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.card.pack_start(self.rows, False, False, 0)
        self.ctx.every(float(self.config.get("interval", 10)), self.refresh)
        return self.card

    def refresh(self) -> None:
        self.ctx.run_async(self.usage.summaries, self._show)

    def _show(self, summaries) -> None:
        self.latest = summaries
        working = any(s.working for s in summaries)
        if working and self.config.get("trigger_dot", True):
            self.ctx.activity.show("agents", dot=True, color=PURPLE, persistent=True, priority=0)
        else:
            self.ctx.activity.clear("agents")
        if not hasattr(self, "rows"):
            return
        for child in self.rows.get_children():
            self.rows.remove(child)
            child.destroy()
        if not summaries:
            self.rows.pack_start(label("No agent activity today", "cw-dim cw-small"), False, False, 0)
        now = dt.datetime.now().astimezone()
        for s in summaries:
            self.rows.pack_start(self._agent(s, now), False, False, 0)
        self.rows.show_all()

    def _agent(self, s, now) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        head = Gtk.Box(spacing=6)
        head.pack_start(label(s.name, "cw-strong cw-small"), False, False, 0)
        if s.models:
            head.pack_start(label(" · ".join(s.models), "cw-dim cw-small", ellipsize=True), True, True, 0)
        state = f"Working in {', '.join(s.working)}" if s.working else ""
        if state:
            head.pack_end(label(state, "cw-agent-working cw-small"), False, False, 0)
        box.pack_start(head, False, False, 0)
        today = f"{human_tokens(s.today_tokens)} tokens today · {s.today_messages} replies"
        box.pack_start(label(today, "cw-dim cw-small"), False, False, 0)
        for limit in s.limits:
            hours = limit.window_minutes / 60 if limit.window_minutes else 0
            name = f"{hours:.0f}-hour limit" if hours and hours < 48 else "Weekly limit" if hours else "Limit"
            resets = f" · resets {limit.resets_at.astimezone(now.tzinfo).strftime('%a %H:%M' if hours >= 24 else '%H:%M')}" \
                if limit.resets_at else ""
            box.pack_start(_meter(f"{name}: {limit.used_percent:.0f}%{resets}", limit.used_percent / 100), False, False, 0)
        if not s.limits and s.window_end is not None:
            left = (s.window_end - now).total_seconds()
            budget = float(self.config.get("window_budget", 0) or 0)
            text = f"5-hour window: {human_tokens(s.window_tokens)} · resets {s.window_end.astimezone(now.tzinfo):%H:%M}" \
                   f" (in {int(left // 3600)} h {int(left % 3600 // 60)} min)"
            fraction = min(1.0, s.window_tokens / budget) if budget > 0 else \
                1 - left / (5 * 3600)  # without a budget: how far through the window we are
            box.pack_start(_meter(text, fraction), False, False, 0)
        return box

    def commands(self) -> list[Command]:
        def run(_arg: str) -> str:
            if not self.latest:
                self.refresh()
                return "No agent activity today"
            return " · ".join(f"{s.name}: {human_tokens(s.today_tokens)} today" for s in self.latest)

        return [Command(("agents", "usage"), "AI agent usage today", "agents", run)]


def _meter(text: str, fraction: float) -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
    box.pack_start(label(text, "cw-small"), False, False, 0)
    bar = Gtk.DrawingArea()
    bar.set_size_request(-1, 5)

    def draw(widget, cr) -> bool:
        from ..ui import theme

        p = theme.current()
        w, h = widget.get_allocated_width(), widget.get_allocated_height()
        pill(cr, 0, 0, w, h)
        cr.set_source_rgba(*p.track)
        cr.fill()
        f = max(0.0, min(1.0, fraction))
        if f > 0:
            pill(cr, 0, 0, max(h, w * f), h)
            color = (1.0, 0.27, 0.23) if f > 0.9 else (1.0, 0.62, 0.04) if f > 0.75 else p.text[:3]
            cr.set_source_rgba(*color, 0.9)
            cr.fill()
        return True

    bar.connect("draw", draw)
    box.pack_start(bar, False, False, 0)
    return box
