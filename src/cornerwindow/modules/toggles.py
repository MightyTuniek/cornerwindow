"""Quick toggles. Each toggle is a tiny plugin:

    @toggle("wifi")
    class Wifi(Toggle):
        title, icon = "Wi-Fi", "wifi"
        def available(self): ...
        def is_on(self): ...      # may block: runs on a worker thread
        def set(self, on): ...    # may block: runs on a worker thread

A toggle can also offer a picker (Wi-Fi networks, Bluetooth devices...): set
`has_picker = True` and implement `picker_items()` and `pick(item)`, both blocking,
both run on a worker thread. Clicking the tile's label (or right-clicking) opens it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gi.repository import GLib, Gtk

from ..core import tasks
from ..core.activity import BLUE, GREEN, ORANGE, PURPLE, RED, YELLOW
from ..ui.widgets import Card, IconButton, ToggleTile, label
from ..ui.widgets.card import resolve_icon, set_active
from .base import Command, Module, module

_TOGGLES: dict[str, type["Toggle"]] = {}


def toggle(id: str):
    def wrap(cls):
        cls.id = id
        _TOGGLES[id] = cls
        return cls

    return wrap


@dataclass
class PickItem:
    label: str
    detail: str = ""
    icon: str = ""
    active: bool = False
    value: Any = None  # handed back to pick()


class Toggle:
    id = ""
    title = ""
    description = ""
    icon = ""
    icon_off: str | None = None
    color = BLUE
    has_picker = False
    picker_reload_after = 0.0  # >0: load the picker again after this many seconds (e.g. a scan)
    settings_panel = ""  # "wifi", "bluetooth"...: adds a "Settings…" row to the picker

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
        """Shown in place of the title, e.g. the network name (runs on a worker thread)."""
        return None

    def picker_items(self) -> list[PickItem]:
        return []

    def pick(self, item: PickItem) -> str | None:
        return None


@toggle("wifi")
class WifiToggle(Toggle):
    title, icon, icon_off, color = "Wi-Fi", "wifi", "wifi-off", BLUE
    description = "Wireless on / off; click the name to pick a network"
    has_picker, settings_panel = True, "wifi"
    picker_reload_after = 4.0

    def available(self) -> bool:
        return self.services.wifi.available()

    def is_on(self) -> bool:
        return self.services.wifi.is_on()

    def set(self, on: bool) -> None:
        self.services.wifi.set(on)

    def subtitle(self) -> str | None:
        return self.services.wifi.connection() or None

    def picker_items(self) -> list[PickItem]:
        if not self.services.wifi.is_on():
            return []
        items = []
        for net in self.services.wifi.networks(rescan=False)[:12]:  # instant; a rescan follows
            detail = "Connected" if net.active else "Saved" if net.known else ""
            items.append(PickItem(net.ssid, detail, _signal_icon(net.signal), net.active, net))
            if net.secure and not net.known:
                items[-1].detail = "Secured"
        return items

    def pick(self, item: PickItem) -> str | None:
        return self.services.wifi.connect(item.value)


def _signal_icon(signal: int) -> str:
    return "wifi" if signal >= 60 else "signal"


@toggle("bluetooth")
class BluetoothToggle(Toggle):
    title, icon, color = "Bluetooth", "bluetooth", BLUE
    description = "Bluetooth on / off; click the name to connect a paired device"
    has_picker, settings_panel = True, "bluetooth"

    def available(self) -> bool:
        return self.services.bluetooth.available()

    def is_on(self) -> bool:
        return self.services.bluetooth.is_on()

    def set(self, on: bool) -> None:
        self.services.bluetooth.set(on)

    def subtitle(self) -> str | None:
        return (self.services.bluetooth.connected_name() or None) if self.is_on() else None

    def picker_items(self) -> list[PickItem]:
        items = []
        for device in self.services.bluetooth.devices():
            parts = ["Connected" if device.connected else ""]
            if device.battery is not None:
                parts.append(f"{device.battery}%")
            items.append(PickItem(device.name, " · ".join(p for p in parts if p),
                                  _bt_icon(device.icon), device.connected, device))
        return items

    def pick(self, item: PickItem) -> str | None:
        return self.services.bluetooth.toggle_device(item.value)


def _bt_icon(kind: str) -> str:
    if "headset" in kind or "headphone" in kind or "audio" in kind:
        return "headphones"
    for needle, icon in (("mouse", "mouse"), ("keyboard", "keyboard"), ("phone", "phone"),
                         ("gaming", "gamepad"), ("joystick", "gamepad"), ("tablet", "tablet")):
        if needle in kind:
            return icon
    return "device"


@toggle("night_light")
class NightLightToggle(Toggle):
    title, icon, color = "Night", "night", ORANGE
    description = "Warmer colours in the evening"

    def available(self) -> bool:
        return self.services.nightlight.available

    def is_on(self) -> bool:
        return self.services.nightlight.is_on()

    def set(self, on: bool) -> None:
        self.services.nightlight.set(on)


@toggle("dark_mode")
class DarkModeToggle(Toggle):
    title, icon, color = "Dark", "contrast", (0.36, 0.36, 0.42)
    description = "The desktop's dark style"

    def available(self) -> bool:
        return self.services.dark_mode.available()

    def is_on(self) -> bool:
        return self.services.dark_mode.is_on()

    def set(self, on: bool) -> None:
        self.services.dark_mode.set(on)


@toggle("dnd")
class DndToggle(Toggle):
    title, icon, color = "Focus", "dnd", PURPLE
    description = "Do not disturb: no notification banners"

    def available(self) -> bool:
        return self.services.dnd.available()

    def is_on(self) -> bool:
        return self.services.dnd.is_on()

    def set(self, on: bool) -> None:
        self.services.dnd.set(on)


@toggle("caffeine")
class CaffeineToggle(Toggle):
    """Keep the screen awake: until switched off, or for a while (the picker, or `awake 1h`)."""

    title, icon, color = "Awake", "caffeine-cup", YELLOW
    description = "Keep the screen from sleeping; click the name for 30 min, 1 h, 2 h"
    has_picker = True

    def available(self) -> bool:
        return self.services.caffeine.available()

    def is_on(self) -> bool:
        return self.services.caffeine.is_on()

    def set(self, on: bool) -> None:
        self.services.caffeine.set(on)
        tasks.on_main(self.module.awake_until, None)  # a plain switch has no end time
        if on:
            self.module.ctx.activity.show("caffeine", dot=True, color=YELLOW, persistent=True, priority=1)
        else:
            self.module.ctx.activity.clear("caffeine")

    def subtitle(self) -> str | None:
        until = self.module.awake_end
        if until is None or not self.services.caffeine.is_on():
            return None
        import time as _time

        return _time.strftime("Until %H:%M", _time.localtime(until))

    def picker_items(self) -> list[PickItem]:
        minutes = [int(m) for m in self.module.config.get("awake_minutes", [30, 60, 120]) or []]
        items = [PickItem(_minutes(m), "", "timer", False, m) for m in minutes]
        items.append(PickItem("Until I switch it off", "", "caffeine", self.is_on() and self.module.awake_end is None, 0))
        if self.is_on():
            items.append(PickItem("Off now", "", "clear", False, -1))
        return items

    def pick(self, item: PickItem) -> str | None:
        if item.value == -1:
            self.set(False)
            return "Awake off"
        self.services.caffeine.set(True)
        self.module.ctx.activity.show("caffeine", dot=True, color=YELLOW, persistent=True, priority=1)
        tasks.on_main(self.module.awake_until, item.value * 60 if item.value else None)
        return f"Awake {item.label.lower()}"


def _minutes(m: int) -> str:
    if m % 60 == 0:
        hours = m // 60
        return f"For {hours} hour{'s' if hours != 1 else ''}"
    return f"For {m} minutes"


@toggle("mic")
class MicToggle(Toggle):
    title, icon, icon_off, color = "Mute mic", "mic-off", "mic", RED  # "on" = muted
    description = "Mute the microphone"

    def available(self) -> bool:
        return self.services.audio.available

    def is_on(self) -> bool:
        return self.services.audio.levels["source"].muted

    def set(self, on: bool) -> None:
        tasks.on_main(self.services.audio.set_muted, "source", on, "toggle")


@toggle("power_profile")
class PowerProfileToggle(Toggle):
    """Lit when not balanced. A click steps through the profiles; the label picks one."""

    title, icon, color = "Power", "profile-balanced", GREEN
    description = "Balanced, performance or power saver (power-profiles-daemon)"
    has_picker, settings_panel = True, "power"
    cycles = True

    def available(self) -> bool:
        return self.services.power_profiles.available()

    def is_on(self) -> bool:
        return self.services.power_profiles.active() not in ("", "balanced")

    def cycle(self) -> None:
        self.services.power_profiles.cycle()

    def set(self, on: bool) -> None:
        self.cycle()

    def subtitle(self) -> str | None:
        from ..platform.power import PROFILE_NAMES

        return PROFILE_NAMES.get(self.services.power_profiles.active())

    def picker_items(self) -> list[PickItem]:
        from ..platform.power import PROFILE_NAMES

        active = self.services.power_profiles.active()
        icons = {"performance": "profile-performance", "balanced": "profile-balanced",
                 "power-saver": "profile-saver"}
        return [PickItem(PROFILE_NAMES.get(p, p), "", icons.get(p, "profile-balanced"), p == active, p)
                for p in self.services.power_profiles.profiles()]

    def pick(self, item: PickItem) -> str | None:
        self.services.power_profiles.set(item.value)
        return f"{item.label} mode"


@toggle("vpn")
class VpnToggle(Toggle):
    title, icon, icon_off, color = "VPN", "vpn", "vpn-off", BLUE
    description = "Your NetworkManager VPN and WireGuard connections"
    has_picker, settings_panel = True, "network"

    def available(self) -> bool:
        return self.services.vpn.available()

    def is_on(self) -> bool:
        return self.services.vpn.is_on()

    def set(self, on: bool) -> None:
        vpn = self.services.vpn
        if on:
            names = [n for n, _a in vpn.connections()]
            last = self.module.ctx.state.get("toggles.vpn.last")
            name = last if last in names else (names[0] if names else None)
            if name:
                vpn.up(name)
        else:
            for name, active in vpn.connections():
                if active:
                    vpn.down(name)

    def subtitle(self) -> str | None:
        return self.services.vpn.active() or None

    def picker_items(self) -> list[PickItem]:
        return [PickItem(name, "Connected" if active else "", "vpn" if active else "vpn-off", active, name)
                for name, active in self.services.vpn.connections()]

    def pick(self, item: PickItem) -> str | None:
        vpn = self.services.vpn
        if item.active:
            vpn.down(item.value)
            return f"{item.value} off"
        for name, active in vpn.connections():
            if active:
                vpn.down(name)
        ok = vpn.up(item.value)
        if ok:
            tasks.on_main(self.module.ctx.state.set, "toggles.vpn.last", item.value)
        return f"{item.value} on" if ok else f"Could not connect {item.value}"


@toggle("airplane")
class AirplaneToggle(Toggle):
    """Every radio off; switching back restores Bluetooth only if it was on before."""

    title, icon, color = "Airplane", "airplane", ORANGE
    description = "Turn Wi-Fi, Bluetooth and mobile broadband off together"

    def available(self) -> bool:
        return self.services.airplane.available()

    def is_on(self) -> bool:
        return bool(self.module.ctx.state.get("toggles.airplane")) and not self.services.airplane.radios_on()

    def set(self, on: bool) -> None:
        state = self.module.ctx.state
        if on:
            bt = self.services.bluetooth
            tasks.on_main(state.set, "toggles.airplane.bt", bool(bt.available() and bt.is_on()))
            self.services.airplane.set(True)
        else:
            self.services.airplane.set(False, restore_bluetooth=bool(state.get("toggles.airplane.bt", True)))
        tasks.on_main(state.set, "toggles.airplane", on)


@toggle("glass")
class GlassToggle(Toggle):
    """The Liquid Glass theme (writes panel.glass, which reloads live)."""

    title, icon, color = "Glass", "glass", (0.35, 0.62, 1.0)
    description = "The Liquid Glass look for the panel"

    def available(self) -> bool:
        from .. import native

        return native.available()  # builds the kernel on first use (worker thread)

    def is_on(self) -> bool:
        return bool(self.module.ctx.config.get("panel.glass", False))

    def set(self, on: bool) -> None:
        tasks.on_main(lambda: self.module.ctx.actions.run("theme.glass", on=on))


@module("toggles", title="Quick toggles", icon="settings", description="Wi-Fi, Bluetooth, night light, focus and more")
class Toggles(Module):
    def setup(self) -> None:
        self.toggles: list[Toggle] = []
        self.tiles: dict[str, ToggleTile] = {}
        self.picking: Toggle | None = None
        self.awake_end: float | None = None  # wall-clock time a timed Awake ends
        self._awake_source = 0
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
        self.picking = None
        card = Card()
        self.grid = Gtk.FlowBox()
        self.grid.set_selection_mode(Gtk.SelectionMode.NONE)
        self.grid.set_homogeneous(True)
        self.grid.set_max_children_per_line(int(self.config.get("per_row", 4)))
        self.grid.set_min_children_per_line(int(self.config.get("per_row", 4)))
        self.grid.set_row_spacing(10)
        card.pack_start(self.grid, False, False, 0)
        # past `rows` rows, the rest wait behind a "More" tile (it opens a second grid)
        self.overflow = Gtk.Revealer()
        self.overflow.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.overflow.set_transition_duration(220)
        self.more_grid = Gtk.FlowBox()
        self.more_grid.set_selection_mode(Gtk.SelectionMode.NONE)
        self.more_grid.set_homogeneous(True)
        self.more_grid.set_max_children_per_line(int(self.config.get("per_row", 4)))
        self.more_grid.set_min_children_per_line(int(self.config.get("per_row", 4)))
        self.more_grid.set_row_spacing(10)
        self.more_grid.set_margin_top(10)
        self.overflow.add(self.more_grid)
        card.pack_start(self.overflow, False, False, 0)
        self.picker = Gtk.Revealer()
        self.picker.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.picker.set_transition_duration(220)
        self.picker_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.picker_box.get_style_context().add_class("cw-picker")
        self.picker.add(self.picker_box)
        card.pack_start(self.picker, False, False, 0)
        self._populate()
        return card

    def _populate(self) -> None:
        def probe():
            return [t for t in self.toggles if _safe_bool(t.available)]

        def done(available: list[Toggle]) -> None:
            room = overflow_split(len(available), int(self.config.get("per_row", 4)),
                                  int(self.config.get("rows", 2)))
            for i, t in enumerate(available):
                tile = ToggleTile(t.icon, t.title, lambda on, t=t: self._set(t, on), t.color,
                                  icon_off=t.icon_off,
                                  on_more=(lambda t=t: self.open_picker(t)) if t.has_picker else None)
                tile.set_tooltip_text(t.description or t.title)
                self.tiles[t.id] = tile
                (self.grid if i < room else self.more_grid).add(tile)
            if room < len(available):
                more = ToggleTile("more", "More", self._show_more, (0.36, 0.36, 0.42))
                more.set_tooltip_text("The other quick toggles")
                self.more_tile = more
                self.grid.add(more)
                if self.ctx.state.get("toggles.more", False):
                    more.set_active(True, animate=False)
                    self.overflow.set_reveal_child(True)
            self.grid.show_all()
            self.more_grid.show_all()
            self.refresh()

        tasks.run_async(probe, done)

    def awake_until(self, seconds: float | None) -> None:
        """Switch Awake off after `seconds` (None: no end). One timer, even while collapsed."""
        import time as _time

        if self._awake_source:
            GLib.source_remove(self._awake_source)
            self._awake_source = 0
        self.awake_end = _time.time() + seconds if seconds else None
        if seconds:
            def end() -> bool:
                self._awake_source = 0
                self.awake_end = None
                tasks.run_async(lambda: self.ctx.services.caffeine.set(False),
                                lambda _r: self.refresh(only=("caffeine",)))
                self.ctx.activity.clear("caffeine")
                return False

            self._awake_source = GLib.timeout_add_seconds(int(seconds), end)
        self.refresh(only=("caffeine",))

    def _show_more(self, on: bool) -> None:
        self.overflow.set_reveal_child(on)
        self.ctx.state.set("toggles.more", on)

    def _set(self, t: Toggle, on: bool) -> None:
        work = t.cycle if getattr(t, "cycles", False) else (lambda: t.set(on))
        tasks.run_async(work, lambda _r: self._after_change(t))

    def _after_change(self, t: Toggle) -> None:
        self.refresh(only=(t.id,))
        if self.picking is t:
            self._load_picker(t)

    def flip(self, t: Toggle) -> str:
        if getattr(t, "cycles", False):
            self._set(t, True)
            return f"{t.title}: next mode"
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
            return [(t.id, _safe_bool(t.is_on), _safe(t.subtitle)) for t in targets]

        def done(states) -> None:
            for tid, on, sub in states:
                tile = self.tiles.get(tid)  # the grid may have been rebuilt meanwhile
                if tile is not None:
                    tile.set_active(on)
                    tile.set_subtitle(sub)

        tasks.run_async(read, done)

    # -- picker ---------------------------------------------------------------------------
    def open_picker(self, t: Toggle) -> None:
        if self.picking is t and self.picker.get_reveal_child():
            self.close_picker()
            return
        self.picking = t
        self._show_picker_rows(t, None)
        self.picker.set_reveal_child(True)
        self._load_picker(t)
        if t.picker_reload_after > 0:
            if t.id == "wifi":
                tasks.run_async(self.ctx.services.wifi.rescan)

            def again() -> bool:
                if self.picking is t:
                    self._load_picker(t)
                return False

            GLib.timeout_add(int(t.picker_reload_after * 1000), again)

    def close_picker(self) -> None:
        self.picking = None
        self.picker.set_reveal_child(False)

    def _load_picker(self, t: Toggle) -> None:
        def done(items) -> None:
            if self.picking is t:
                self._show_picker_rows(t, items)

        tasks.run_async(lambda: _safe(t.picker_items) or [], done)

    def _show_picker_rows(self, t: Toggle, items: list[PickItem] | None) -> None:
        box = self.picker_box
        for child in box.get_children():
            box.remove(child)
            child.destroy()
        head = Gtk.Box(spacing=6)
        head.pack_start(label(t.title, "cw-title"), True, True, 0)
        head.pack_end(IconButton("clear", self.close_picker, "Close", "cw-icon cw-plain", 12), False, False, 0)
        box.pack_start(head, False, False, 0)
        if items is None:
            box.pack_start(label("Looking…", "cw-dim cw-small"), False, False, 4)
        elif not items:
            empty = {"wifi": "Wi-Fi is off" if not _safe_bool(t.is_on) else "No networks found",
                     "bluetooth": "No paired devices"}.get(t.id, "Nothing here")
            box.pack_start(label(empty, "cw-dim cw-small"), False, False, 4)
        for item in (items or [])[:10]:
            box.pack_start(self._pick_row(t, item), False, False, 0)
        if t.settings_panel:
            box.pack_start(self._settings_row(t), False, False, 0)
        box.show_all()

    def _pick_row(self, t: Toggle, item: PickItem) -> Gtk.Widget:
        button = Gtk.Button()
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_can_focus(False)
        button.get_style_context().add_class("cw-row")
        set_active(button, item.active)
        line = Gtk.Box(spacing=8)
        if item.icon:
            image = Gtk.Image.new_from_icon_name(resolve_icon(item.icon), Gtk.IconSize.MENU)
            image.set_pixel_size(14)
            line.pack_start(image, False, False, 0)
        line.pack_start(label(item.label, "", ellipsize=True), True, True, 0)
        if item.detail:
            line.pack_end(label(item.detail, "cw-dim cw-small"), False, False, 0)
        button.add(line)

        def picked(*_a) -> None:
            button.set_sensitive(False)

            tasks.run_async(lambda: t.pick(item), lambda _message: self._after_change(t))

        button.connect("clicked", picked)
        return button

    def _settings_row(self, t: Toggle) -> Gtk.Widget:
        from ..platform.desktop import open_settings

        button = Gtk.Button()
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_can_focus(False)
        button.get_style_context().add_class("cw-row")
        button.add(label(f"{t.title} settings…", "cw-dim cw-small"))

        def go(*_a) -> None:
            open_settings(t.settings_panel)
            self.ctx.island.close()

        button.connect("clicked", go)
        return button

    def on_open(self) -> None:
        self.refresh()

    def on_close(self) -> None:
        if getattr(self, "picker", None) is not None and self.picking is not None:
            self.picking = None
            GLib.idle_add(lambda: self.picker.set_reveal_child(False) and False)

    def commands(self) -> list[Command]:
        cmds = [Command((t.id.replace("_", ""), t.title.lower()), f"Toggle {t.title}", t.title.lower(),
                        lambda _a, t=t: self.flip(t)) for t in self.toggles if t.id in self.tiles]
        bt = next((t for t in self.toggles if t.id == "bluetooth" and t.id in self.tiles), None)
        if bt is not None:
            cmds.append(Command(("bt",), "Connect a Bluetooth device", "bt headphones",
                                lambda arg, t=bt: self._pick_by_name(t, arg), True))
        awake = next((t for t in self.toggles if t.id == "caffeine" and t.id in self.tiles), None)
        if awake is not None:
            cmds.append(Command(("awake",), "Keep the screen awake for a while", "awake 1h · awake 30 · awake off",
                                lambda arg, t=awake: self._awake_for(t, arg), True))
        wifi = next((t for t in self.toggles if t.id == "wifi" and t.id in self.tiles), None)
        if wifi is not None:
            cmds.append(Command(("join",), "Join a Wi-Fi network", "join home",
                                lambda arg, t=wifi: self._pick_by_name(t, arg), True))
        return cmds

    def _awake_for(self, t: Toggle, arg: str) -> str:
        from .timer import _human, _parse_duration

        arg = arg.strip().lower()
        if arg in ("off", "stop"):
            tasks.run_async(lambda: t.set(False), lambda _r: self._after_change(t))
            return "Awake off"
        seconds = _parse_duration(arg) if arg else None
        if arg and seconds is None:
            return "Usage: awake 1h · awake 30 · awake off"
        item = PickItem("", value=round(seconds / 60) if seconds else 0)
        tasks.run_async(lambda: t.pick(item), lambda _r: self._after_change(t))
        return f"Awake for {_human(seconds)}" if seconds else "Awake until you switch it off"

    def _pick_by_name(self, t: Toggle, arg: str) -> str:
        arg = arg.strip().lower()
        if not arg:
            self.open_picker(t)
            return f"Pick a {t.title} entry"

        def work() -> str | None:
            for item in t.picker_items():
                if arg in item.label.lower():
                    return t.pick(item)
            return f"No {t.title} entry matches {arg!r}"

        tasks.run_async(work, lambda _m: self._after_change(t))
        return f"{t.title}: {arg}…"


def overflow_split(count: int, per_row: int, rows: int) -> int:
    """How many tiles go in the main grid; the rest sit behind a "More" tile, which takes the
    last slot. rows <= 0 = no limit."""
    if rows <= 0 or per_row <= 0 or count <= per_row * rows:
        return count
    return per_row * rows - 1


def _safe_bool(fn) -> bool:
    try:
        return bool(fn())
    except Exception:
        return False


def _safe(fn):
    try:
        return fn()
    except Exception:
        return None
