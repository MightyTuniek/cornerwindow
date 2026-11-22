"""Spring physics and a frame-clock driver.

Springs use the same parameterisation as Apple's SwiftUI springs:
  response          ~ period of the undamped oscillation, in seconds
  damping_fraction  1.0 = critically damped (no overshoot), < 1 = bouncy

Springs keep their velocity when the target changes, so interrupting an
animation half way (click to open, click again to close) reverses smoothly.
"""

from __future__ import annotations

import math
import os
import time
from typing import Callable, Iterable

SUBSTEP = 1.0 / 480.0  # fixed integration step, independent of the frame rate
MAX_FRAME_DT = 1.0 / 20.0  # after a stall, never jump further than this


class Spring:
    __slots__ = ("value", "target", "velocity", "stiffness", "damping", "precision", "on_settle")

    def __init__(self, value: float = 0.0, response: float = 0.4, damping_fraction: float = 0.85,
                 precision: float = 0.001) -> None:
        self.value = float(value)
        self.target = float(value)
        self.velocity = 0.0
        self.precision = precision
        self.on_settle: Callable[[], None] | None = None
        self.configure(response, damping_fraction)

    def configure(self, response: float, damping_fraction: float) -> "Spring":
        response = max(response, 0.01)
        self.stiffness = (2.0 * math.pi / response) ** 2
        self.damping = 4.0 * math.pi * damping_fraction / response
        return self

    def to(self, target: float, response: float | None = None,
           damping_fraction: float | None = None) -> "Spring":
        if response is not None and damping_fraction is not None:
            self.configure(response, damping_fraction)
        self.target = float(target)
        return self

    def snap(self, value: float) -> None:
        self.value = self.target = float(value)
        self.velocity = 0.0

    def kick(self, velocity: float) -> None:
        self.velocity += velocity

    @property
    def settled(self) -> bool:
        return (abs(self.value - self.target) < self.precision
                and abs(self.velocity) < self.precision * 20)

    def step(self, dt: float) -> bool:
        """Advance by dt seconds. Returns True while still moving."""
        if self.settled:
            if self.value != self.target:
                self.value = self.target
                self.velocity = 0.0
            return False
        remaining = dt
        while remaining > 1e-9:
            h = min(SUBSTEP, remaining)
            accel = -self.stiffness * (self.value - self.target) - self.damping * self.velocity
            self.velocity += accel * h  # semi-implicit Euler: stable for stiff springs
            self.value += self.velocity * h
            remaining -= h
        if self.settled:
            self.value = self.target
            self.velocity = 0.0
            if self.on_settle:
                self.on_settle()
            return False
        return True


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def progress(x: float, start: float, end: float) -> float:
    """Map x from [start, end] to [0, 1], clamped."""
    if end == start:
        return 1.0 if x >= end else 0.0
    return clamp((x - start) / (end - start))


def ease_out_cubic(t: float) -> float:
    t = clamp(t)
    return 1 - (1 - t) ** 3


def rubber_band(overshoot: float, dimension: float, coefficient: float = 0.55) -> float:
    """iOS-style rubber banding: resistance grows the further you pull."""
    if dimension <= 0:
        return 0.0
    sign = 1 if overshoot >= 0 else -1
    x = abs(overshoot)
    return sign * (1.0 - (1.0 / (x * coefficient / dimension + 1.0))) * dimension


class FrameStats:
    def __init__(self) -> None:
        self.frames: list[float] = []

    def add(self, dt: float) -> None:
        self.frames.append(dt)

    def report(self) -> str | None:
        if len(self.frames) < 3:
            self.frames.clear()
            return None
        frames = self.frames[1:]
        avg = sum(frames) / len(frames)
        worst = max(frames)
        self.frames.clear()
        return f"{len(frames)} frames, avg {1 / avg:.1f} fps, worst {worst * 1000:.1f} ms"


class Animator:
    """Drives a set of springs from a GTK widget's frame clock.

    It only ticks while something is moving, so an idle CornerWindow costs 0% CPU.
    """

    def __init__(self, widget, on_frame: Callable[[], None], springs: Iterable[Spring] = (),
                 slow_motion: float = 1.0) -> None:
        self.widget = widget
        self.on_frame = on_frame
        self.springs: list[Spring] = list(springs)
        self.slow_motion = max(slow_motion, 0.05)
        self._tick_id = 0
        self._last: float | None = None
        self._extra_frames = 0
        self.stats = FrameStats() if os.environ.get("CORNERWINDOW_FPS") else None

    def add(self, *springs: Spring) -> None:
        self.springs.extend(springs)

    def kick(self, extra_frames: int = 0) -> None:
        """Make sure we're ticking. extra_frames keeps ticking even when settled
        (used by things like a pulsing indicator that isn't spring driven)."""
        self._extra_frames = max(self._extra_frames, extra_frames)
        if not self._tick_id:
            self._last = None
            self._tick_id = self.widget.add_tick_callback(self._tick)

    @property
    def running(self) -> bool:
        return bool(self._tick_id)

    def _tick(self, widget, clock) -> bool:
        now = clock.get_frame_time() / 1_000_000.0 if clock else time.monotonic()
        if self._last is None:
            dt = 1.0 / 60.0
            try:
                timings = clock.get_current_timings()
                interval = timings.get_refresh_interval() if timings else 0
                if interval:
                    dt = interval / 1_000_000.0
            except Exception:
                pass
        else:
            dt = now - self._last
        self._last = now
        dt = min(max(dt, 0.0), MAX_FRAME_DT) / self.slow_motion
        if self.stats is not None:
            self.stats.add(dt * self.slow_motion)

        moving = False
        for spring in self.springs:
            if spring.step(dt):
                moving = True
        self.on_frame()

        if self._extra_frames > 0:
            self._extra_frames -= 1
            moving = True
        if not moving:
            self._tick_id = 0
            if self.stats is not None:
                summary = self.stats.report()
                if summary:
                    print(f"[cornerwindow fps] {summary}", flush=True)
            return False
        return True
