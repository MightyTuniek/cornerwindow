"""AI agents: which coding agents need you, which are working, how close you are to your plan
limits, and what each used today (Claude Code, Codex, OpenCode, Gemini CLI, Copilot CLI, Amp, pi,
Goose and more, see platform/agents.py).

Layout, top to bottom: agents that wait for you, then working, done, open, then the rest by
tokens today. Each agent: a state dot, its name and what it does where, one dim line of models,
tokens and cost, and its limits. A limit meter fills with the % used, and a thin tick marks how
far through the window we are, so "almost at the reset" never looks like "almost out": fill
behind the tick = on track. Agents with nothing going on fold into one "Also today" line.
Read from local files only. Hidden when no such tool is installed; off by default."""

from __future__ import annotations

import datetime as dt

from gi.repository import Gtk

from ..core.activity import ORANGE, PURPLE
from ..platform.agents import REPORTS, AgentUsage, human_cost, human_tokens, pace
from ..ui.draw import pill
from ..ui.widgets import Card, label
from .base import Command, Module, module

WAITING, WORKING, DONE = (1.0, 0.624, 0.039), (0.749, 0.353, 0.949), (0.188, 0.82, 0.345)
WARN, DANGER = (1.0, 0.624, 0.039), (1.0, 0.271, 0.227)


@module("agents", title="AI agents", icon="agent",
        description="Claude Code, Codex, OpenCode, Gemini and more: who needs you, plan limits, usage today")
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
        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
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
        reports = REPORTS.current()
        waiting = any(s.waiting for s in summaries) or any(r.state == "waiting" for r in reports)
        working = any(s.working for s in summaries) or any(r.state == "working" for r in reports)
        if (waiting or working) and self.config.get("trigger_dot", True):
            # orange when one waits for you (like a window asking for attention), purple while they work
            self.ctx.activity.show("agents", dot=True, color=ORANGE if waiting else PURPLE, persistent=True,
                                   priority=1 if waiting else 0)
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
        now = dt.datetime.now().astimezone()
        ordered = sorted(summaries, key=_rank)
        active = [s for s in ordered if _state(s)[0] or s.limits or s.window_end is not None]
        quiet = [s for s in ordered if s not in active]
        if not active:  # nothing going on: everyone gets a row
            active, quiet = quiet, []
        if not active:
            self.rows.pack_start(label("No agent activity today", "cw-dim cw-small"), False, False, 0)
        for s in active:
            self.rows.pack_start(self._agent(s, now), False, False, 0)
        if quiet:
            self.rows.pack_start(_also_today(quiet), False, False, 0)
        self.rows.show_all()

    def _agent(self, s, now) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        state, color = _state(s)
        head = Gtk.Box(spacing=7)
        head.pack_start(_Dot(color), False, False, 0)
        head.pack_start(label(s.name, "cw-strong"), False, False, 0)
        if state:
            css = "cw-agent-waiting" if s.waiting else "cw-agent-working" if s.working else "cw-dim"
            head.pack_end(label(state, f"{css} cw-small", xalign=1.0, ellipsize=True), True, True, 0)
        box.pack_start(head, False, False, 0)
        today = _today(s)
        if today:
            line = label(today, "cw-dim cw-small cw-indent", ellipsize=True)
            line.set_tooltip_text(f"{s.today_messages} replies today, {s.today_tokens:,} tokens"
                                  " (cache reads not counted)")
            box.pack_start(line, False, False, 0)
        for limit in s.limits:
            box.pack_start(_limit_meter(limit, now), False, False, 0)
        if not s.limits and s.window_end is not None:
            box.pack_start(self._window(s, now), False, False, 0)
        return box

    def _window(self, s, now) -> Gtk.Widget:
        """Claude without its status line: the window and its tokens, which say nothing about the
        plan's %, so no meter (unless you set a token budget yourself)."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.get_style_context().add_class("cw-indent")
        box.get_style_context().add_class("cw-limit")
        budget = float(self.config.get("window_budget", 0) or 0)
        left = (s.window_end - now).total_seconds()
        # a guess from this PC's logs: use on claude.ai or elsewhere moves the window, so never a promise
        reset = f"Resets around {s.window_end.astimezone(now.tzinfo):%H:%M} · in {_span(left)}"
        box.set_tooltip_text("Guessed from Claude Code's logs on this PC. Claude on the web, in the desktop "
                             "chat or on another device shares the window and can move it.")
        if budget > 0:
            used = s.window_tokens / budget
            box.pack_start(_row("5-hour window (estimate)", f"{used * 100:.0f}% of your budget"), False, False, 0)
            box.pack_start(_Meter(used, 1 - left / (5 * 3600), _tone(used * 100, None)), False, False, 0)
            box.pack_start(label(reset, "cw-dim cw-small"), False, False, 0)
            return box
        box.pack_start(_row("5-hour window (estimate)", f"{human_tokens(s.window_tokens)} tokens"), False, False, 0)
        box.pack_start(label(reset, "cw-dim cw-small"), False, False, 0)
        hint = label("Real limit %: run the Claude desktop app, or agent setup", "cw-dim cw-small", ellipsize=True)
        hint.set_tooltip_text("The Claude desktop app reads your plan usage every 15 minutes and the card picks it "
                              "up. In a terminal, `cornerwindow agent setup` makes Claude Code's status line pass "
                              "the exact limits on (keeping your status line).")
        box.pack_start(hint, False, False, 0)
        return box

    def commands(self) -> list[Command]:
        def run(_arg: str) -> str:
            if not self.latest:
                self.refresh()
                return "No agent activity today"
            now = dt.datetime.now().astimezone()
            parts = []
            for s in sorted(self.latest, key=_rank):
                text = f"{s.name}: {_state(s)[0] or 'idle'}"
                for limit in s.limits:
                    text += f", {_limit_name(limit).lower()} {limit.used_percent:.0f}%"
                    if limit.resets_at:
                        text += f" (resets in {_span((limit.resets_at - now).total_seconds())})"
                if s.today_tokens:
                    text += f", {human_tokens(s.today_tokens)} today"
                parts.append(text)
            return " · ".join(parts)

        return [Command(("agents", "usage", "limits"), "AI agents: who needs you, limits, usage today", "agents", run)]


# -- what each row says ----------------------------------------------------------------------
def _rank(s) -> tuple:
    order = 0 if s.waiting else 1 if s.working else 2 if s.done else 3 if s.running else 4
    return order, -s.today_tokens, s.name


def _state(s) -> tuple[str, tuple | None]:
    if s.waiting:
        return f"Needs you in {_places(s.waiting)}", WAITING
    if s.working:
        return f"Working in {_places(s.working)}", WORKING
    if s.done:
        return f"Done in {_places(s.done)}", DONE
    if s.running:
        return f"Open in {_places(s.running)}", None
    return "", None


def _today(s) -> str:
    parts = [" · ".join(s.models[:2]) + (f" +{len(s.models) - 2}" if len(s.models) > 2 else "")] if s.models else []
    if s.today_tokens:
        parts.append(f"{human_tokens(s.today_tokens)} tokens today")
    if s.today_cost:
        parts.append(human_cost(s.today_cost))
    return " · ".join(p for p in parts if p)


def _places(projects: list[str]) -> str:
    """Two project names at most, so the line fits: 'app, site +2'."""
    more = f" +{len(projects) - 2}" if len(projects) > 2 else ""
    return ", ".join(projects[:2]) + more


def _also_today(quiet) -> Gtk.Widget:
    parts = []
    for s in quiet:
        text = f"{s.name} {human_tokens(s.today_tokens)}" if s.today_tokens else s.name
        parts.append(text + (f" ({human_cost(s.today_cost)})" if s.today_cost else ""))
    line = label("Also today: " + " · ".join(parts), "cw-dim cw-small")
    line.set_line_wrap(True)
    return line


def _limit_name(limit) -> str:
    hours = limit.window_minutes / 60 if limit.window_minutes else 0
    return f"{hours:.0f}-hour limit" if hours and hours < 48 else "Weekly limit" if hours else "Limit"


def _span(seconds: float) -> str:
    """1 h 20 min · 25 min · 2 d 4 h."""
    minutes = max(0, int(seconds // 60))
    if minutes >= 48 * 60:
        return f"{minutes // 1440} d {minutes % 1440 // 60} h"
    if minutes >= 60:
        return f"{minutes // 60} h {minutes % 60} min"
    return f"{minutes} min"


def _tone(used: float, runs_out) -> tuple | None:
    """Red when nearly out, orange when getting close or on pace to run out before the reset."""
    if used >= 90:
        return DANGER
    if used >= 75 or runs_out is not None:
        return WARN
    return None


def _limit_meter(limit, now: dt.datetime) -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    box.get_style_context().add_class("cw-indent")
    box.get_style_context().add_class("cw-limit")
    p = pace(limit, now)
    used = limit.used_percent
    box.pack_start(_row(_limit_name(limit), f"{used:.0f}% used"), False, False, 0)
    box.pack_start(_Meter(used / 100, p.elapsed if p else None, _tone(used, p and p.runs_out)), False, False, 0)
    weekly = limit.window_minutes >= 48 * 60
    notes, warn = [], False
    if limit.resets_at is not None:
        at = limit.resets_at.astimezone(now.tzinfo)
        when = f"{at:%a %H:%M}" if weekly else f"{at:%H:%M}"
        # by = only the latest it can be; ~ = worked out to a few minutes; plain = Claude said so
        when = f"by {when}" if limit.latest else f"~{when}" if limit.approx else when
        notes.append(f"Resets {when}" if weekly or limit.latest else
                     f"Resets {when} · in {_span((limit.resets_at - now).total_seconds())}")
    if p and p.runs_out is not None:
        notes.append(f"runs out ~{p.runs_out.astimezone(now.tzinfo):%a %H:%M}" if weekly else
                     f"runs out ~{p.runs_out.astimezone(now.tzinfo):%H:%M} at this pace")
        warn = True
    elif p and p.projected is not None and used < 100:
        notes.append(f"on pace for ~{min(p.projected, 100):.0f}%")
    if limit.as_of is not None and (now - limit.as_of).total_seconds() > 15 * 60:
        notes.append(f"as of {limit.as_of.astimezone(now.tzinfo):%H:%M}")
    if notes:
        box.pack_start(label(" · ".join(notes), "cw-agent-waiting cw-small" if warn else "cw-dim cw-small",
                             ellipsize=True), False, False, 0)
    box.set_tooltip_text("The bar fills with how much of the limit is used. The tick marks how far "
                         "through the window we are: fill behind the tick means you're on track.")
    return box


def _row(left: str, right: str) -> Gtk.Widget:
    row = Gtk.Box(spacing=6)
    row.pack_start(label(left, "cw-small"), False, False, 0)
    row.pack_end(label(right, "cw-small cw-strong cw-num", xalign=1.0), False, False, 0)
    return row


class _Dot(Gtk.DrawingArea):
    """The state dot before an agent's name: orange waits for you, purple works, green is done,
    a dim ring is open or idle."""

    def __init__(self, color: tuple | None) -> None:
        super().__init__()
        self.color = color
        self.set_size_request(8, 8)
        self.set_valign(Gtk.Align.CENTER)
        self.connect("draw", self._draw)

    def _draw(self, widget, cr) -> bool:
        import math

        from ..ui import theme

        w, h = widget.get_allocated_width(), widget.get_allocated_height()
        cr.arc(w / 2, h / 2, 3.5 if self.color else 3, 0, 2 * math.pi)
        if self.color:
            cr.set_source_rgba(*self.color, 1)
            cr.fill()
        else:
            cr.set_source_rgba(*theme.current().text_dim[:3], 0.6)
            cr.set_line_width(1.2)
            cr.stroke()
        return True


class _Meter(Gtk.DrawingArea):
    """A bar filled to `used` (0..1) with a tick at `elapsed` (0..1, how far through the window)."""

    def __init__(self, used: float, elapsed: float | None, tone: tuple | None) -> None:
        super().__init__()
        self.used, self.elapsed, self.tone = used, elapsed, tone
        self.set_size_request(-1, 12)
        self.connect("draw", self._draw)

    def _draw(self, widget, cr) -> bool:
        from ..ui import theme

        p = theme.current()
        w = widget.get_allocated_width()
        h, y = 6, 3
        pill(cr, 0, y, w, h)
        cr.set_source_rgba(*p.track)
        cr.fill()
        f = max(0.0, min(1.0, self.used))
        if f > 0:
            pill(cr, 0, y, max(h, w * f), h)
            cr.set_source_rgba(*(self.tone or p.text[:3]), 0.9)
            cr.fill()
        if self.elapsed is not None and 0.02 < self.elapsed < 0.98:
            x = round(w * self.elapsed) + 0.5
            cr.set_source_rgba(*p.text[:3], 0.85)
            cr.set_line_width(1.5)
            cr.move_to(x, 0)
            cr.line_to(x, y + h + 3)  # pokes out of the bar, so it shows over the fill too
            cr.stroke()
        return True
