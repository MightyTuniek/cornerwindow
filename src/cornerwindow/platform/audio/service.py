"""AudioService: cached levels, optimistic updates, coalesced writes."""

from __future__ import annotations

from ...core import log, tasks
from ...core.bus import EventBus
from .base import AppVolume, AudioBackend, Device, Level, group_streams
from .pactl import PactlBackend
from .wpctl import WpctlBackend

_log = log.get("audio")


def pick_backend(choice: str = "auto") -> AudioBackend | None:
    candidates = {"wpctl": WpctlBackend, "pactl": PactlBackend}
    order = list(candidates) if choice == "auto" else [choice]
    for name in order:
        cls = candidates.get(name)
        if cls and cls.available():
            return cls()
    return None


class AudioService:
    def __init__(self, config: dict, bus: EventBus) -> None:
        self.config = config
        self.bus = bus
        self.backend = pick_backend(config.get("backend", "auto"))
        self.levels: dict[str, Level] = {"sink": Level(0.5, False), "source": Level(1.0, False)}
        self.max_volume = float(config.get("max_volume", 100)) / 100.0
        self._writer = tasks.LatestWins(self._apply, min_interval=0.02, name="audio-writer")
        if self.backend:
            _log.info("Audio via %s", self.backend.name)

    @property
    def available(self) -> bool:
        return self.backend is not None

    def friendly(self, device: Device, kind: str = "sink") -> str:
        names = self.config.get("names" if kind == "sink" else "input_names", {}) or {}
        for needle, label in names.items():
            if needle.lower() in device.name.lower():
                return label
        name = device.name
        for noise in (" Analog Stereo", " Digital Stereo", " Controller", "Family 17h (Models 00h-0fh) "):
            name = name.replace(noise, "")
        return name.strip()

    def refresh(self, then=None) -> None:
        if not self.backend or self._writer.busy():
            return

        def read() -> dict[str, Level]:
            return {kind: self.backend.get(kind) for kind in ("sink", "source")}

        def done(levels: dict[str, Level]) -> None:
            if self._writer.busy():
                return
            for kind, level in levels.items():
                if level != self.levels.get(kind):
                    self.levels[kind] = level
                    self.bus.publish("audio.changed", kind=kind, level=level, source="refresh")
            if then:
                then()

        tasks.run_async(read, done)

    def set_volume(self, kind: str, volume: float, source: str = "") -> float:
        volume = max(0.0, min(self.max_volume, volume))
        level = Level(volume, self.levels[kind].muted if volume > 0 else self.levels[kind].muted)
        self.levels[kind] = level
        self._writer.submit((kind, "volume"), volume)
        if level.muted and volume > 0 and source != "refresh":
            self.set_muted(kind, False, source)
        self.bus.publish("audio.changed", kind=kind, level=level, source=source)
        return volume

    def step(self, kind: str, delta: float, source: str = "") -> float:
        return self.set_volume(kind, round(self.levels[kind].volume * 100 + delta) / 100.0, source)

    def set_muted(self, kind: str, muted: bool, source: str = "") -> None:
        self.levels[kind] = Level(self.levels[kind].volume, muted)
        self._writer.submit((kind, "mute"), muted)
        self.bus.publish("audio.changed", kind=kind, level=self.levels[kind], source=source)

    def toggle_mute(self, kind: str, source: str = "") -> bool:
        muted = not self.levels[kind].muted
        self.set_muted(kind, muted, source)
        return muted

    def devices(self, kind: str, then) -> None:
        if self.backend:
            tasks.run_async(lambda: self.backend.devices(kind), then)

    def set_default(self, kind: str, device_id: str, then=None) -> None:
        if self.backend:
            tasks.run_async(lambda: self.backend.set_default(kind, device_id),
                            lambda _: self.refresh(then))

    # -- per-app volume ------------------------------------------------------------------
    def apps(self, then) -> None:
        """Calls then(list[AppVolume]) with the apps playing sound right now."""
        if not self.backend:
            then([])
            return
        ignore = [str(p) for p in self.config.get("mixer_ignore", ["speech-dispatcher"]) or []]
        tasks.run_async(lambda: group_streams(self.backend.streams(), ignore), then)

    def set_app_volume(self, app: AppVolume, volume: float) -> None:
        app.volume = max(0.0, min(self.max_volume, volume))
        for stream_id in app.ids:
            self._writer.submit((("stream", stream_id), "volume"), app.volume)

    def set_app_muted(self, app: AppVolume, muted: bool) -> None:
        app.muted = muted
        for stream_id in app.ids:
            self._writer.submit((("stream", stream_id), "mute"), muted)

    def _apply(self, key, value) -> None:
        kind, what = key
        if isinstance(kind, tuple):  # ("stream", id)
            if what == "volume":
                self.backend.set_stream_volume(kind[1], value)
            else:
                self.backend.set_stream_muted(kind[1], value)
            return
        if what == "volume":
            self.backend.set_volume(kind, value)
        else:
            self.backend.set_muted(kind, value)
