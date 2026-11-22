from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Device:
    id: str
    name: str
    default: bool = False


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
