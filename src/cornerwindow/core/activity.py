"""Live activities: tiny glanceable states shown on the collapsed trigger.

A module calls e.g.
    ctx.activity.show("display.brightness", level=0.6, label="60", color=YELLOW, timeout=1.4)
    ctx.activity.show("timer", level=0.25, color=ORANGE, persistent=True, priority=5)
    ctx.activity.clear("timer")

The trigger renders the most important one: it grows a little, draws a level
arc inside the black quarter-circle and an optional label or dot.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

# Apple system colours (dark appearance)
WHITE = (1.0, 1.0, 1.0)
YELLOW = (1.0, 0.839, 0.039)
ORANGE = (1.0, 0.624, 0.039)
RED = (1.0, 0.271, 0.227)
GREEN = (0.188, 0.82, 0.345)
BLUE = (0.039, 0.518, 1.0)
PURPLE = (0.369, 0.361, 0.902)


@dataclass
class Activity:
    id: str
    level: float | None = None  # 0..1 arc, None = no arc
    label: str | None = None  # short text, e.g. "60"
    dot: bool = False  # a small solid dot (e.g. mic muted)
    color: tuple[float, float, float] = WHITE
    priority: int = 0  # transient OSDs use 10, persistent states lower
    persistent: bool = False
    expires: float = 0.0
    radius: float | None = None  # how big the trigger grows; None = automatic
    created: float = field(default_factory=time.monotonic)


class ActivityManager:
    def __init__(self) -> None:
        self._items: dict[str, Activity] = {}
        self._listeners: list[Callable[[], None]] = []
        self._timer_id = 0

    def on_change(self, callback: Callable[[], None]) -> None:
        self._listeners.append(callback)

    def show(self, id: str, *, level: float | None = None, label: str | None = None,
             dot: bool = False, color=WHITE, priority: int | None = None,
             persistent: bool = False, timeout: float = 1.4, radius: float | None = None) -> None:
        if priority is None:
            priority = 3 if persistent else 10
        self._items[id] = Activity(
            id=id, level=level, label=label, dot=dot, color=color, priority=priority,
            persistent=persistent, radius=radius,
            expires=0.0 if persistent else time.monotonic() + timeout,
        )
        self._schedule_expiry()
        self._notify()

    def clear(self, id: str) -> None:
        if self._items.pop(id, None) is not None:
            self._notify()

    def current(self) -> Activity | None:
        self._drop_expired()
        if not self._items:
            return None
        return max(self._items.values(), key=lambda a: (a.priority, a.created))

    def _drop_expired(self) -> bool:
        now = time.monotonic()
        expired = [k for k, a in self._items.items() if not a.persistent and a.expires <= now]
        for key in expired:
            del self._items[key]
        return bool(expired)

    def _schedule_expiry(self) -> None:
        from gi.repository import GLib

        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        pending = [a.expires for a in self._items.values() if not a.persistent]
        if not pending:
            return
        delay = max(0.0, min(pending) - time.monotonic())

        def fire() -> bool:
            self._timer_id = 0
            if self._drop_expired():
                self._notify()
            self._schedule_expiry()
            return False

        self._timer_id = GLib.timeout_add(int(delay * 1000) + 10, fire)

    def _notify(self) -> None:
        for callback in list(self._listeners):
            callback()
