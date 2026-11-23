"""Notification history: what popped up while you weren't looking. The desktop keeps showing
its banners; this card collects them. Hidden while empty; a blue dot on the corner while
something is unread."""

from __future__ import annotations

import time

from gi.repository import Gio, Gtk, Pango

from ..core.activity import BLUE
from ..platform.notifications import ago
from ..ui.widgets import Card, IconButton, label
from ..ui.widgets.card import resolve_icon
from .base import Command, Module, Suggestion, module


@module("notifications", title="Notifications", icon="notification",
        description="Recent notifications, even ones you missed; hidden while there are none")
class Notifications(Module):
    def setup(self) -> None:
        self.history = self.ctx.services.notifications
        self.ctx.bus.subscribe("notifications.changed", self._changed)
        self.expanded = False

    def available(self) -> bool:
        return bool(getattr(self.history, "monitor", None) and self.history.monitor.running)

    def build(self):
        self.revealer = Gtk.Revealer()
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.revealer.set_transition_duration(240)
        card = Card("Notifications", spacing=6)
        card.header.pack_end(IconButton("clear", self.clear, "Clear all", "cw-icon cw-plain cw-mini", 12),
                             False, False, 0)
        self.count = label("", "cw-dim cw-small")
        card.header.pack_end(self.count, False, False, 4)
        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        card.pack_start(self.rows, False, False, 0)
        self.more = Gtk.Button()
        self.more.set_relief(Gtk.ReliefStyle.NONE)
        self.more.set_can_focus(False)
        self.more.get_style_context().add_class("cw-disclosure")
        self.more.set_halign(Gtk.Align.START)
        self.more.add(label("", "cw-dim cw-small"))
        self.more.connect("clicked", lambda *_: self._expand(not self.expanded))
        self.more.set_no_show_all(True)
        card.pack_start(self.more, False, False, 0)
        self.revealer.add(card)
        self._render()
        self.ctx.every(30, self._render, immediately=False)  # keep "5 min" ages fresh
        return self.revealer

    def _changed(self, **_kw) -> None:
        if self.config.get("trigger_dot", True) and self.history.unread:
            self.ctx.activity.show("notifications", dot=True, color=BLUE, persistent=True, priority=1)
        if hasattr(self, "rows"):
            self._render()

    def _expand(self, on: bool) -> None:
        self.expanded = on
        self._render()

    def _render(self) -> None:
        if not hasattr(self, "rows"):
            return
        for child in self.rows.get_children():
            self.rows.remove(child)
            child.destroy()
        items = self.history.items
        shown = items if self.expanded else items[: int(self.config.get("show", 3))]
        now = time.time()
        for item in shown:
            self.rows.pack_start(self._row(item, now), False, False, 0)
        hidden = len(items) - len(shown)
        self.more.get_child().set_text(f"{hidden} more" if hidden > 0 else "Show fewer")
        self.more.set_visible(hidden > 0 or (self.expanded and len(items) > int(self.config.get("show", 3))))
        self.count.set_text(str(len(items)) if len(items) > 1 else "")
        self.rows.show_all()
        self.revealer.set_reveal_child(bool(items))

    def _row(self, item, now: float) -> Gtk.Widget:
        button = Gtk.Button()
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_can_focus(False)
        button.get_style_context().add_class("cw-row")
        line = Gtk.Box(spacing=8)
        image = _icon(item)
        image.set_valign(Gtk.Align.START)
        line.pack_start(image, False, False, 0)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        head = Gtk.Box(spacing=6)
        head.pack_start(label(item.summary, "cw-strong cw-small", ellipsize=True), True, True, 0)
        head.pack_end(label(ago(now - item.at), "cw-dim cw-small"), False, False, 0)
        texts.pack_start(head, False, False, 0)
        if item.body:
            body = label(item.body, "cw-dim cw-small")
            body.set_line_wrap(True)
            body.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            body.set_lines(2)
            body.set_ellipsize(Pango.EllipsizeMode.END)
            body.set_max_width_chars(1)
            body.set_hexpand(True)
            texts.pack_start(body, False, False, 0)
        line.pack_start(texts, True, True, 0)
        close = IconButton("close", lambda i=item: self._dismiss(i), "Dismiss", "cw-icon cw-plain cw-mini", 10)
        close.set_valign(Gtk.Align.START)
        line.pack_start(close, False, False, 0)
        button.add(line)
        button.set_tooltip_text(f"{item.app}: open it" if item.desktop_entry else item.app)
        button.connect("clicked", lambda _b, i=item: self._open(i))
        return button

    def _open(self, item) -> None:
        self._dismiss(item)
        if item.desktop_entry:
            try:
                info = Gio.DesktopAppInfo.new(f"{item.desktop_entry}.desktop")
                if info is not None:
                    info.launch([], None)
                    self.ctx.island.close()
            except Exception:
                pass

    def _dismiss(self, item) -> None:
        self.history.remove(item.id)
        self._render()

    def clear(self) -> str:
        self.history.clear()
        self.ctx.activity.clear("notifications")
        self._render()
        return "Notifications cleared"

    def on_open(self) -> None:
        self.history.seen()
        self.ctx.activity.clear("notifications")
        self._render()

    def commands(self) -> list[Command]:
        def suggest(arg: str) -> list[Suggestion]:
            words = arg.lower().split()
            items = [n for n in self.history.items
                     if all(w in f"{n.app} {n.summary} {n.body}".lower() for w in words)]
            if not items:
                return [Suggestion("No notifications match" if words else "No notifications", "", lambda: None,
                                   "notification")]
            now = time.time()
            return [Suggestion(f"{n.summary}", f"{n.app} · {ago(now - n.at)}", lambda n=n: self._open(n) or "Opened",
                               "notification") for n in items[:7]]

        return [
            Command(("notifications", "nn"), "Search notifications", "nn · nn invoice", lambda a: None, True,
                    suggest=suggest),
            Command(("dismiss",), "Clear notifications", "dismiss", lambda _a: self.clear()),
        ]


def _icon(item) -> Gtk.Image:
    image = None
    icon = item.icon
    try:
        if icon.startswith("/") or icon.startswith("file://"):
            gicon = Gio.FileIcon.new(Gio.File.new_for_uri(icon) if icon.startswith("file://")
                                     else Gio.File.new_for_path(icon))
            image = Gtk.Image.new_from_gicon(gicon, Gtk.IconSize.MENU)
        elif icon and Gtk.IconTheme.get_default().has_icon(icon):
            image = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.MENU)
        elif item.desktop_entry:
            info = Gio.DesktopAppInfo.new(f"{item.desktop_entry}.desktop")
            if info is not None and info.get_icon() is not None:
                image = Gtk.Image.new_from_gicon(info.get_icon(), Gtk.IconSize.MENU)
    except Exception:
        image = None
    if image is None:
        image = Gtk.Image.new_from_icon_name(resolve_icon("notification"), Gtk.IconSize.MENU)
    image.set_pixel_size(22)
    return image
