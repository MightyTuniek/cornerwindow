"""PulseAudio (or pipewire-pulse) via `pactl`."""

from __future__ import annotations

import re

from .. import proc
from .base import AudioBackend, Device, Level

_DEFAULT = {"sink": "@DEFAULT_SINK@", "source": "@DEFAULT_SOURCE@"}


class PactlBackend(AudioBackend):
    name = "pactl"

    @staticmethod
    def available() -> bool:
        return proc.which("pactl") is not None and bool(proc.run(["pactl", "info"], 2))

    def get(self, kind: str) -> Level:
        vol = proc.run(["pactl", f"get-{kind}-volume", _DEFAULT[kind]], 2)
        mute = proc.run(["pactl", f"get-{kind}-mute", _DEFAULT[kind]], 2)
        percents = [int(p) for p in re.findall(r"(\d+)%", vol)]
        volume = (sum(percents) / len(percents) / 100.0) if percents else 0.0
        return Level(volume, "yes" in mute.lower())

    def set_volume(self, kind: str, volume: float) -> None:
        proc.run(["pactl", f"set-{kind}-volume", _DEFAULT[kind], f"{round(max(0.0, volume) * 100)}%"], 2)

    def set_muted(self, kind: str, muted: bool) -> None:
        proc.run(["pactl", f"set-{kind}-mute", _DEFAULT[kind], "1" if muted else "0"], 2)

    def devices(self, kind: str) -> list[Device]:
        default = proc.run(["pactl", f"get-default-{kind}"], 2).strip()
        devices = []
        current: dict[str, str] = {}
        for line in proc.run(["pactl", "list", f"{kind}s"], 3).splitlines() + [""]:
            line = line.strip()
            if line.startswith("Name:"):
                current["name"] = line.split(":", 1)[1].strip()
            elif line.startswith("Description:"):
                current["desc"] = line.split(":", 1)[1].strip()
            elif not line and current.get("name"):
                if not current["name"].endswith(".monitor"):
                    devices.append(Device(current["name"], current.get("desc", current["name"]),
                                          current["name"] == default))
                current = {}
        return devices

    def set_default(self, kind: str, device_id: str) -> None:
        proc.run(["pactl", f"set-default-{kind}", device_id], 2)
