from __future__ import annotations

from abc import ABC, abstractmethod
import json
from dataclasses import dataclass


@dataclass
class Device:
    id: str
    name: str
    default: bool = False


@dataclass
class Stream:
    """One app's playback stream (an app may have several, e.g. browser tabs)."""

    id: str
    app: str
    icon: str  # icon name or app id ("" = unknown)
    volume: float  # 0..1, same scale as Level
    muted: bool


@dataclass
class AppVolume:
    """Every stream of one app, moved together by one slider."""

    app: str
    icon: str
    ids: list[str]
    volume: float
    muted: bool


@dataclass
class Level:
    volume: float  # 0..1 (may exceed 1 when boosted)
    muted: bool


class AudioBackend(ABC):
    name = "base"

    @staticmethod
    @abstractmethod
    def available() -> bool: ...

    @abstractmethod
    def get(self, kind: str) -> Level:
        """kind = 'sink' (output) or 'source' (microphone)."""

    @abstractmethod
    def set_volume(self, kind: str, volume: float) -> None: ...

    @abstractmethod
    def set_muted(self, kind: str, muted: bool) -> None: ...

    @abstractmethod
    def devices(self, kind: str) -> list[Device]: ...

    @abstractmethod
    def set_default(self, kind: str, device_id: str) -> None: ...

    # Per-app volume (optional: backends without it report no streams)
    def streams(self) -> list[Stream]:
        return []

    def set_stream_volume(self, stream_id: str, volume: float) -> None:
        pass

    def set_stream_muted(self, stream_id: str, muted: bool) -> None:
        pass


def parse_pw_dump(text: str) -> list[Stream]:
    """Playback streams from `pw-dump`. PipeWire stores linear amplitude; wpctl and the
    sliders use its cube root, so that is what Stream.volume holds."""
    try:
        objects = json.loads(text or "[]")
    except ValueError:
        return []
    streams = []
    for obj in objects if isinstance(objects, list) else []:
        info = obj.get("info") or {}
        props = info.get("props") or {}
        if props.get("media.class") != "Stream/Output/Audio":
            continue
        params = (info.get("params") or {}).get("Props") or [{}]
        values = next((p for p in params if "channelVolumes" in p or "mute" in p), params[0]) or {}
        channels = values.get("channelVolumes") or [values.get("volume", 1.0)]
        linear = max(float(v) for v in channels) if channels else 1.0
        streams.append(Stream(
            id=str(obj.get("id")),
            app=str(props.get("application.name") or props.get("node.name") or "App"),
            icon=str(props.get("application.icon-name") or props.get("application.id")
                     or props.get("application.process.binary") or ""),
            volume=round(max(0.0, linear) ** (1 / 3), 3),
            muted=bool(values.get("mute", False)),
        ))
    return streams


def parse_pactl_streams(text: str) -> list[Stream]:
    """Playback streams from `pactl -f json list sink-inputs` (PulseAudio 16+)."""
    try:
        items = json.loads(text or "[]")
    except ValueError:
        return []
    streams = []
    for item in items if isinstance(items, list) else []:
        props = item.get("properties") or {}
        percents = []
        for channel in (item.get("volume") or {}).values():
            try:
                percents.append(float(str(channel.get("value_percent", "100%")).rstrip("%")))
            except (AttributeError, ValueError):
                pass
        streams.append(Stream(
            id=str(item.get("index")),
            app=str(props.get("application.name") or props.get("media.name") or "App"),
            icon=str(props.get("application.icon_name") or props.get("application.process.binary") or ""),
            volume=(max(percents) / 100.0) if percents else 1.0,
            muted=bool(item.get("mute", False)),
        ))
    return streams


def group_streams(streams: list[Stream], ignore: list[str] = ()) -> list[AppVolume]:
    """One entry per app, in first-seen order; apps matching `ignore` are left out."""
    groups: dict[str, AppVolume] = {}
    for stream in streams:
        if any(pattern and pattern.lower() in stream.app.lower() for pattern in ignore):
            continue
        group = groups.get(stream.app)
        if group is None:
            groups[stream.app] = AppVolume(stream.app, stream.icon, [stream.id], stream.volume, stream.muted)
        else:
            group.ids.append(stream.id)
            group.volume = max(group.volume, stream.volume)
            group.muted = group.muted and stream.muted
            group.icon = group.icon or stream.icon
    return list(groups.values())
