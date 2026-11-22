"""A tiny publish/subscribe bus.

Handlers always run on the GTK main thread: publishing from a worker thread
is safe and gets marshalled through GLib.idle_add.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any, Callable

from . import log

_log = log.get("bus")
Handler = Callable[..., Any]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._main_thread = threading.main_thread()

    def subscribe(self, topic: str, handler: Handler) -> Callable[[], None]:
        self._handlers[topic].append(handler)

        def unsubscribe() -> None:
            if handler in self._handlers[topic]:
                self._handlers[topic].remove(handler)

        return unsubscribe

    def publish(self, topic: str, **payload: Any) -> None:
        if threading.current_thread() is self._main_thread:
            self._dispatch(topic, payload)
        else:
            from gi.repository import GLib

            GLib.idle_add(self._dispatch_once, topic, payload)

    def _dispatch_once(self, topic: str, payload: dict) -> bool:
        self._dispatch(topic, payload)
        return False

    def _dispatch(self, topic: str, payload: dict) -> None:
        for handler in list(self._handlers.get(topic, ())):
            try:
                handler(**payload)
            except Exception:  # a broken module must never take the bus down
                _log.exception("Handler for %s failed", topic)
