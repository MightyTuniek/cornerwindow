"""Notification history: we watch the session bus for Notify calls instead of replacing the
desktop's own notification server (GNOME Shell, KDE, mako, dunst keep showing the banners).

D-Bus monitoring needs its own connection (a monitor can't send anything else), opened once.
Parsing is pure, for the tests.
"""

from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from ..core import log

_log = log.get("notifications")
RULE = "type='method_call',interface='org.freedesktop.Notifications',member='Notify'"
_TAGS = re.compile(r"<[^>]+>")


@dataclass
class Notification:
    app: str
    summary: str
    body: str = ""
    icon: str = ""  # an icon name or a file path
    desktop_entry: str = ""  # "org.gnome.Nautilus": click launches it
    at: float = field(default_factory=time.time)
    urgent: bool = False
    id: int = 0  # our own counter


def from_notify(args: tuple, at: float | None = None) -> Notification | None:
    """The arguments of org.freedesktop.Notifications.Notify ->
    (app_name, replaces_id, app_icon, summary, body, actions, hints, expire_timeout)."""
    try:
        app, _replaces, icon, summary, body, _actions, hints, _timeout = args
    except (TypeError, ValueError):
        return None
    hints = dict(hints or {})
    summary = clean(summary)
    body = clean(body)
    if not summary and not body:
        return None
    entry = str(hints.get("desktop-entry") or "")
    image = str(hints.get("image-path") or hints.get("image_path") or "")
    urgency = hints.get("urgency")
    return Notification(app=str(app or entry or "App"), summary=summary or body, body=body if summary else "",
                        icon=str(icon or image or entry or ""), desktop_entry=entry,
                        at=time.time() if at is None else at, urgent=urgency in (2, b"\x02"))


def clean(text: str) -> str:
    """Notification bodies may carry a little markup (<b>, <a href>) and entities."""
    return " ".join(html.unescape(_TAGS.sub("", str(text or ""))).split())


class History:
    """Newest first, capped; `same app + same summary` replaces the older entry (chat apps resend)."""

    def __init__(self, size: int = 30) -> None:
        self.size = size
        self.items: list[Notification] = []
        self.unread = 0
        self._next = 1

    def add(self, item: Notification) -> None:
        item.id = self._next
        self._next += 1
        self.items = [n for n in self.items if not (n.app == item.app and n.summary == item.summary)]
        self.items.insert(0, item)
        del self.items[self.size:]
        self.unread += 1

    def remove(self, item_id: int) -> None:
        self.items = [n for n in self.items if n.id != item_id]

    def clear(self) -> None:
        self.items.clear()
        self.unread = 0

    def seen(self) -> None:
        self.unread = 0


def ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "now"
    if seconds < 3600:
        return f"{seconds // 60} min"
    if seconds < 86400:
        return f"{seconds // 3600} h"
    return f"{seconds // 86400} d"


class Monitor:
    """Calls `on_notification(Notification)` on the main thread for every notification shown."""

    def __init__(self, on_notification: Callable[[Notification], None], ignore: list[str] = ()) -> None:
        self.on_notification = on_notification
        self.ignore = [i.lower() for i in ignore if i]
        self.connection = None
        self.running = False

    def start(self) -> bool:
        """Open a private connection and ask the bus to make it a monitor (asynchronously: the
        UI never waits). `running` turns True once the bus agrees."""
        from gi.repository import Gio, GLib

        try:
            address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
            flags = (Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                     | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION)
            self.connection = Gio.DBusConnection.new_for_address_sync(address, flags, None, None)
        except Exception as exc:
            _log.info("Notification history unavailable: %s", exc)
            self.connection = None
            return False
        self.connection.add_filter(self._filter)
        self.running = True  # optimistic; turned off if the bus refuses

        def done(conn, result) -> None:
            try:
                conn.call_finish(result)
            except Exception as exc:
                _log.info("Notification history unavailable: %s", exc)
                self.running = False

        self.connection.call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus.Monitoring",
                             "BecomeMonitor", GLib.Variant("(asu)", ([RULE], 0)), None, Gio.DBusCallFlags.NONE,
                             5000, None, done)
        return True

    def _filter(self, _connection, message, incoming: bool):
        # runs on the GDBus worker thread
        from gi.repository import Gio

        kind = message.get_message_type()
        if not incoming or kind in (Gio.DBusMessageType.METHOD_RETURN, Gio.DBusMessageType.ERROR):
            return message  # our own BecomeMonitor call going out, and its answer coming back
        try:
            if incoming and kind == Gio.DBusMessageType.METHOD_CALL and message.get_member() == "Notify":
                body = message.get_body()
                item = from_notify(body.unpack() if body is not None else ())
                if item is not None and not any(i in item.app.lower() for i in self.ignore):
                    from ..core import tasks

                    tasks.on_main(self.on_notification, item)
        except Exception:
            _log.debug("Could not read a notification", exc_info=True)
        return None  # a monitor only listens: never let GDBus answer what it overhears
