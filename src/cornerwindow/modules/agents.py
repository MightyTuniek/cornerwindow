"""AI agents: usage today for every coding agent with local logs (Claude Code, Codex, OpenCode,
Gemini CLI, Copilot CLI, Amp, pi, Goose and more, see platform/agents.py), cost where the tool
records it, Claude's 5-hour window, Codex's plan limits, and which projects have an agent working
or waiting for you (from the agents' own hooks, herdr, or recently written logs).
Read from local files only. Hidden when no such tool is installed; off by default."""

from __future__ import annotations

import datetime as dt

from gi.repository import Gtk

from ..core.activity import PURPLE
from ..platform.agents import REPORTS, AgentUsage, human_cost, human_tokens
from ..ui.draw import pill
from ..ui.widgets import Card, label
from .base import Command, Module, module


@module("agents", title="AI agents", icon="agent",
        description="Claude Code, Codex, OpenCode, Gemini and more: tokens and cost today, limits, who's working")
class Agents(Module):
    def setup(self) -> None:
        self.usage = AgentUsage()
        self.latest = []

    def configure(self) -> None:
        self.usage.configure(hide=self.config.get("hide", []), herdr=bool(self.config.get("herdr", True)),
                             processes=bool(self.config.get("processes", True)))

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

    def reported(self) -> None:
        """An agent's hook said what it's doing: update the dot now, even with the panel shut."""
        self._dot(self.latest)
        if hasattr(self, "rows") and self.rows.get_mapped():
            self.refresh()

    def _dot(self, summaries) -> None:
        busy = any(s.working or s.waiting for s in summaries) or \
            any(r.state in ("working", "waiting") for r in REPORTS.current())
        if busy and self.config.get("trigger_dot", True):
            self.ctx.activity.show("agents", dot=True, color=PURPLE, persistent=True, priority=0)
        else:
            self.ctx.activity.clear("agents")

    def _show(self, summaries) -> None:
        self.latest = summaries
        self._dot(summaries)
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
        state = f"Waiting for you in {_places(s.waiting)}" if s.waiting else \
            f"Working in {_places(s.working)}" if s.working else f"Done in {_places(s.done)}" if s.done else \
            f"Open in {_places(s.running)}" if s.running else ""
        if state:
            head.pack_end(label(state, "cw-dim cw-small" if not (s.waiting or s.working) else "cw-agent-working cw-small"),
                          False, False, 0)
        box.pack_start(head, False, False, 0)
        if s.today_messages:
            today = f"{human_tokens(s.today_tokens)} tokens today · {s.today_messages} replies"
            if s.today_cost:
                today += f" · {human_cost(s.today_cost)}"
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
            return " · ".join(f"{s.name}: {human_tokens(s.today_tokens)} today"
                              + (f" ({human_cost(s.today_cost)})" if s.today_cost else "") for s in self.latest)

        return [Command(("agents", "usage"), "AI agent usage today", "agents", run)]


def _places(projects: list[str]) -> str:
    """Two project names at most, so the line fits: 'app, site +2'."""
    more = f" +{len(projects) - 2}" if len(projects) > 2 else ""
    return ", ".join(projects[:2]) + more


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
