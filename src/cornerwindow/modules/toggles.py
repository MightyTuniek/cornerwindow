"""Quick toggles. Each toggle is a tiny plugin:

    @toggle("wifi")
    class WifiToggle(Toggle):
        title, icon, color = "Wi-Fi", "wifi", BLUE
        def available(self): ...
        def is_on(self): ...      # may block: runs on a worker thread
        def set(self, on): ...    # may block: runs on a worker thread
"""

from __future__ import annotations

from gi.repository import Gtk

from ..core import tasks
from ..core.activity import BLUE, ORANGE, PURPLE, RED, YELLOW
from ..ui.widgets import Card, ToggleTile
from .base import Command, Module, module

_TOGGLES: dict[str, type["Toggle"]] = {}


def toggle(id: str):
    def wrap(cls):
        cls.id = id
        _TOGGLES[id] = cls
        return cls

    return wrap


class Toggle:
    id = ""
    title = ""
    icon = ""
    icon_off: str | None = None
    color = BLUE

    def __init__(self, services, module: "Toggles") -> None:
        self.services = services
        self.module = module

    def available(self) -> bool:
        return True

    def is_on(self) -> bool:
        return False

    def set(self, on: bool) -> None:
        pass

    def subtitle(self) -> str | None:
        return None


@toggle("wifi")
class WifiToggle(Toggle):
    title, icon, icon_off, color = "Wi-Fi", "wifi", "wifi-off", BLUE

    def available(self) -> bool:
        return self.services.wifi.available()

    def is_on(self) -> bool:
        return self.services.wifi.is_on()

    def set(self, on: bool) -> None:
        self.services.wifi.set(on)

    def subtitle(self) -> str | None:
        return self.services.wifi.connection() or None


@toggle("bluetooth")
class BluetoothToggle(Toggle):
    title, icon, color = "Bluetooth", "bluetooth", BLUE

    def available(self) -> bool:
        return self.services.bluetooth.available()

    def is_on(self) -> bool:
        return self.services.bluetooth.is_on()

    def set(self, on: bool) -> None:
        self.services.bluetooth.set(on)


@toggle("night_light")
class NightLightToggle(Toggle):
    title, icon, color = "Night", "night", ORANGE

    def available(self) -> bool:
        return self.services.nightlight.available

    def is_on(self) -> bool:
        return self.services.nightlight.is_on()

    def set(self, on: bool) -> None:
        self.services.nightlight.set(on)


@toggle("dark_mode")
class DarkModeToggle(Toggle):
    title, icon, color = "Dark", "contrast", (0.36, 0.36, 0.42)

    def available(self) -> bool:
        return self.services.dark_mode.available()

    def is_on(self) -> bool:
        return self.services.dark_mode.is_on()

    def set(self, on: bool) -> None:
        self.services.dark_mode.set(on)


@toggle("dnd")
class DndToggle(Toggle):
    title, icon, color = "Focus", "dnd", PURPLE

    def available(self) -> bool:
        return self.services.dnd.available()

    def is_on(self) -> bool:
        return self.services.dnd.is_on()

    def set(self, on: bool) -> None:
        self.services.dnd.set(on)


@toggle("caffeine")
class CaffeineToggle(Toggle):
    title, icon, color = "Awake", "caffeine-cup", YELLOW

    def available(self) -> bool:
        return self.services.caffeine.available()

    def is_on(self) -> bool:
        return self.services.caffeine.is_on()

    def set(self, on: bool) -> None:
        self.services.caffeine.set(on)
        if on:
            self.module.ctx.activity.show("caffeine", dot=True, color=YELLOW, persistent=True, priority=1)
        else:
            self.module.ctx.activity.clear("caffeine")


@toggle("mic")
class MicToggle(Toggle):
    title, icon, icon_off, color = "Mute mic", "mic-off", "mic", RED  # "on" = muted

    def available(self) -> bool:
        return self.services.audio.available

    def is_on(self) -> bool:
        return self.services.audio.levels["source"].muted

    def set(self, on: bool) -> None:
        tasks.on_main(self.services.audio.set_muted, "source", on, "toggle")


@module("toggles", title="Quick toggles", icon="settings")
class Toggles(Module):
    def setup(self) -> None:
        self.toggles: list[Toggle] = []
        self.tiles: dict[str, ToggleTile] = {}
        self.ctx.bus.subscribe("audio.changed", lambda **_: self.refresh(only=("mic",)))

    def configure(self) -> None:
        self.toggles = []
        for tid in self.config.get("items", list(_TOGGLES)):
            cls = _TOGGLES.get(tid)
            if cls is not None:
                self.toggles.append(cls(self.ctx.services, self))
        for t in self.toggles:
            self.ctx.actions.register(f"toggle.{t.id}", lambda t=t: self.flip(t), f"Toggle {t.title}")

    def build(self):
        self.tiles = {}
        card = Card()
        self.grid = Gtk.FlowBox()
        self.grid.set_selection_mode(Gtk.SelectionMode.NONE)
        self.grid.set_homogeneous(True)
        self.grid.set_max_children_per_line(int(self.config.get("per_row", 4)))
        self.grid.set_min_children_per_line(int(self.config.get("per_row", 4)))
        self.grid.set_row_spacing(10)
        card.pack_start(self.grid, False, False, 0)
        self._populate()
        return card

    def _populate(self) -> None:
        def probe():
            return [t for t in self.toggles if _safe_bool(t.available)]

        def done(available: list[Toggle]) -> None:
            for t in available:
                tile = ToggleTile(t.icon, t.title, lambda on, t=t: self._set(t, on), t.color,
                                  icon_off=t.icon_off)
                self.tiles[t.id] = tile
                self.grid.add(tile)
            self.grid.show_all()
            self.refresh()

        tasks.run_async(probe, done)

    def _set(self, t: Toggle, on: bool) -> None:
        tasks.run_async(lambda: t.set(on), lambda _r: self.refresh(only=(t.id,)))

    def flip(self, t: Toggle) -> str:
        on = not _safe_bool(t.is_on)
        self._set(t, on)
        tile = self.tiles.get(t.id)
        if tile:
            tile.set_active(on)
        return f"{t.title} {'on' if on else 'off'}"

    def refresh(self, only: tuple[str, ...] | None = None) -> None:
        targets = [t for t in self.toggles if t.id in self.tiles and (only is None or t.id in only)]
        if not targets:
            return

        def read():
            return [(t.id, _safe_bool(t.is_on), t.subtitle()) for t in targets]

        def done(states) -> None:
            for tid, on, _sub in states:
                self.tiles[tid].set_active(on)

        tasks.run_async(read, done)

    def on_open(self) -> None:
        self.refresh()

    def commands(self) -> list[Command]:
        return [Command((t.id.replace("_", ""), t.title.lower()), f"Toggle {t.title}", t.title.lower(),
                        lambda _a, t=t: self.flip(t)) for t in self.toggles if t.id in self.tiles]


def _safe_bool(fn) -> bool:
    try:
        return bool(fn())
    except Exception:
        return False
