"""Background work that never blocks the UI thread.

* `run_async(fn, then)`   run fn on a worker pool, deliver the result on the main thread.
* `LatestWins`            a dedicated worker that applies only the newest value per key
                          and spaces writes out. Perfect for sliders driving slow hardware
                          (DDC/CI, wpctl): dragging never queues up a backlog.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Hashable

from . import log

_log = log.get("tasks")
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="cw-worker")


def on_main(fn: Callable, *args: Any) -> None:
    from gi.repository import GLib

    def _call() -> bool:
        try:
            fn(*args)
        except Exception:
            _log.exception("Main-thread callback failed")
        return False

    GLib.idle_add(_call)


def run_async(fn: Callable[[], Any], then: Callable[[Any], None] | None = None,
              error: Callable[[BaseException], None] | None = None) -> None:
    def job() -> None:
        try:
            result = fn()
        except Exception as exc:  # report, never crash the pool
            _log.debug("Async task failed: %s", exc, exc_info=True)
            if error:
                on_main(error, exc)
            return
        if then:
            on_main(then, result)

    _pool.submit(job)


class LatestWins:
    """Coalescing writer: submit(key, value) many times, only the last value is applied."""

    def __init__(self, apply: Callable[[Hashable, Any], None], min_interval: float = 0.05,
                 name: str = "latest-wins") -> None:
        self._apply = apply
        self._min_interval = min_interval
        self._pending: dict[Hashable, Any] = {}
        self._cond = threading.Condition()
        self._last_write = 0.0
        self._thread = threading.Thread(target=self._loop, name=name, daemon=True)
        self._thread.start()

    def submit(self, key: Hashable, value: Any) -> None:
        with self._cond:
            self._pending[key] = value
            self._cond.notify()

    def busy(self) -> bool:
        with self._cond:
            return bool(self._pending)

    def _loop(self) -> None:
        while True:
            with self._cond:
                while not self._pending:
                    self._cond.wait()
                key = next(iter(self._pending))
                value = self._pending.pop(key)
            wait = self._min_interval - (time.monotonic() - self._last_write)
            if wait > 0:
                time.sleep(wait)
                # A newer value may have arrived while we slept; prefer it.
                with self._cond:
                    if key in self._pending:
                        value = self._pending.pop(key)
            try:
                self._apply(key, value)
            except Exception:
                _log.exception("Applying %r=%r failed", key, value)
            self._last_write = time.monotonic()
