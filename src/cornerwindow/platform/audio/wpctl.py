"""PipeWire / WirePlumber via `wpctl`."""

from __future__ import annotations

import re

from .. import proc
from .base import AudioBackend, Device, Level, Stream, parse_pw_dump, pw_serials

_TARGET = {"sink": "@DEFAULT_AUDIO_SINK@", "source": "@DEFAULT_AUDIO_SOURCE@"}
_SECTION = {"sink": "Sinks:", "source": "Sources:"}


def parse_volume(text: str) -> Level:
    match = re.search(r"Volume:\s+([\d.]+)", text)
    if not match:
        raise RuntimeError(f"unexpected wpctl output {text!r}")
    return Level(float(match.group(1)), "[MUTED]" in text)


def parse_status(text: str, kind: str) -> list[Device]:
    """Pull devices of one kind out of the 'Audio' part of `wpctl status`."""
    devices: list[Device] = []
    in_audio = False
    in_section = False
    for line in text.splitlines():
        stripped = line.strip(" │├└─")
        if line.startswith("Audio"):
            in_audio = True
            continue
        if in_audio and line and not line.startswith((" ", "│", "├", "└")):
            break  # next top-level section (Video, Settings)
        if not in_audio:
            continue
        if stripped.endswith(":") and stripped.rstrip(":") in ("Devices", "Sinks", "Sources", "Filters", "Streams"):
            in_section = stripped == _SECTION[kind]
            continue
        if not in_section:
            continue
        match = re.match(r"^(\*)?\s*(\d+)\.\s+(.+?)(?:\s+\[vol:.*\])?$", stripped.strip())
        if match:
            devices.append(Device(match.group(2), match.group(3).strip(), bool(match.group(1))))
        elif not stripped.strip():
            in_section = False
    return devices


class WpctlBackend(AudioBackend):
    name = "wpctl"

    @staticmethod
    def available() -> bool:
        return proc.which("wpctl") is not None and bool(proc.run(["wpctl", "get-volume", _TARGET["sink"]], 2))

    def get(self, kind: str) -> Level:
        return parse_volume(proc.run(["wpctl", "get-volume", _TARGET[kind]], 2))

    def set_volume(self, kind: str, volume: float) -> None:
        proc.run(["wpctl", "set-volume", _TARGET[kind], f"{max(0.0, volume):.3f}"], 2)

    def set_muted(self, kind: str, muted: bool) -> None:
        proc.run(["wpctl", "set-mute", _TARGET[kind], "1" if muted else "0"], 2)

    def devices(self, kind: str) -> list[Device]:
        return parse_status(proc.run(["wpctl", "status"], 3), kind)

    def set_default(self, kind: str, device_id: str) -> None:
        proc.run(["wpctl", "set-default", device_id], 2)

    def streams(self) -> list[Stream]:
        if not proc.which("pw-dump"):
            return []
        return parse_pw_dump(proc.run(["pw-dump"], 3))

    def set_stream_volume(self, stream_id: str, volume: float) -> None:
        proc.run(["wpctl", "set-volume", stream_id, f"{max(0.0, volume):.3f}"], 2)

    def set_stream_muted(self, stream_id: str, muted: bool) -> None:
        proc.run(["wpctl", "set-mute", stream_id, "1" if muted else "0"], 2)

    def move_stream(self, stream_id: str, sink_id: str) -> None:
        # WirePlumber follows the stream's target.object metadata (the sink's serial)
        serial = pw_serials(proc.run(["pw-dump"], 3)).get(sink_id, sink_id) if proc.which("pw-dump") else sink_id
        proc.run(["pw-metadata", stream_id, "target.object", serial, "Spa:Id"], 2)
