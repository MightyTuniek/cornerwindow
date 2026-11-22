"""Service container handed to every module as `ctx.services`.

Services are created lazily on first use, so a disabled module costs nothing.
To add a service: add a cached_property here (and a backend under platform/).
"""

from __future__ import annotations

from functools import cached_property

from .config import Config
from .core.bus import EventBus
from .core.state import State


class Services:
    def __init__(self, config: Config, bus: EventBus, state: State) -> None:
        self.config = config
        self.bus = bus
        self.state = state

    def reconfigure(self, config: Config) -> None:
        """Push a new config into services that already exist (they keep their threads/state)."""
        self.config = config
        if "display" in self.__dict__:
            self.display.config = config.section("display")
        if "audio" in self.__dict__:
            self.audio.config = config.section("audio")
            self.audio.max_volume = float(config.section("audio").get("max_volume", 100)) / 100.0

    @cached_property
    def display(self):
        from .platform.display.service import DisplayService

        service = DisplayService(self.config.section("display"), self.bus)
        service.start()
        return service

    @cached_property
    def nightlight(self):
        from .platform.display.nightlight import NightLight

        return NightLight(self.state)

    @cached_property
    def audio(self):
        from .platform.audio.service import AudioService

        return AudioService(self.config.section("audio"), self.bus)

    @cached_property
    def media(self):
        from .platform.media.mpris import MediaService

        service = MediaService(self.bus)
        service.start()
        return service

    @cached_property
    def wifi(self):
        from .platform.network import WiFi

        return WiFi()

    @cached_property
    def bluetooth(self):
        from .platform.network import Bluetooth

        return Bluetooth()

    @cached_property
    def dark_mode(self):
        from .platform.desktop import DarkMode

        return DarkMode()

    @cached_property
    def dnd(self):
        from .platform.desktop import DoNotDisturb

        return DoNotDisturb()

    @cached_property
    def caffeine(self):
        from .platform.power import Caffeine

        return Caffeine()

    @cached_property
    def backdrop(self):
        """What is behind our windows, for the Liquid Glass theme (live on X11)."""
        from .platform import backdrop

        return backdrop.create(live=bool(self.config.get("glass.live", True)))

    @cached_property
    def clipboard(self):
        from .platform.clipboard import ClipboardHistory

        section = self.config.section("clipboard")
        return ClipboardHistory(int(section.get("size", 10)), bool(section.get("persist", False)))
