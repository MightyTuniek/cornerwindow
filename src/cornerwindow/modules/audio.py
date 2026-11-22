"""Output volume, mute, output device switching, microphone mute."""

from __future__ import annotations

from gi.repository import Gtk

from ..core.activity import RED, WHITE
from ..ui.widgets import Card, IconButton, PillSlider, chip
from ..ui.widgets.card import set_active
from .base import Command, Module, module


@module("audio", title="Sound", icon="volume")
class Audio(Module):
    def available(self) -> bool:
        return self.svc.available

    def setup(self) -> None:
        self.svc = self.ctx.services.audio
        self.ctx.actions.register("audio.volume.step", lambda delta=1: self.nudge(delta), "Volume up/down")
        self.ctx.actions.register("audio.mute", lambda: self.toggle_mute("sink"), "Mute output")
        self.ctx.actions.register("audio.mic.mute", lambda: self.toggle_mute("source"), "Mute microphone")
        self.ctx.bus.subscribe("audio.changed", self._changed)
        if self.svc.available:
            self.svc.refresh(self._mic_activity)

    def configure(self) -> None:
        self.step = float(self.config.get("step", 5))

    def nudge(self, delta: float) -> None:
        if not self.svc.available:
            return
        volume = self.svc.step("sink", delta * self.step, source="nudge")
        self.ctx.activity.show("audio.volume", level=min(volume, 1.0), label=f"{round(volume * 100)}",
                               color=WHITE)

    def toggle_mute(self, kind: str) -> str:
        if not self.svc.available:
            return "No audio"
        muted = self.svc.toggle_mute(kind, source="action")
        what = "Microphone" if kind == "source" else "Sound"
        if kind == "sink":
            self.ctx.activity.show("audio.mute", dot=muted, level=None if muted else self.svc.levels["sink"].volume,
                                   color=RED if muted else WHITE, timeout=1.2)
        self._mic_activity()
        return f"{what} {'muted' if muted else 'on'}"

    def _mic_activity(self) -> None:
        if self.config.get("show_mic_muted", True) and self.svc.levels["source"].muted:
            self.ctx.activity.show("audio.mic", dot=True, color=RED, persistent=True, priority=2)
        else:
            self.ctx.activity.clear("audio.mic")

    # -- UI -------------------------------------------------------------------------------
    def build(self):
        card = Card("Sound")
        row = Gtk.Box(spacing=8)
        self.slider = PillSlider("volume", self._slid, self.svc.levels["sink"].volume, step=self.step / 100)
        row.pack_start(self.slider, True, True, 0)
        self.mic = IconButton("mic", lambda: self.toggle_mute("source"), "Microphone", "cw-icon cw-big")
        row.pack_start(self.mic, False, False, 0)
        card.pack_start(row, False, False, 0)
        self.devices_box = Gtk.FlowBox()
        self.devices_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.devices_box.set_column_spacing(6)
        self.devices_box.set_row_spacing(6)
        self.devices_box.set_max_children_per_line(4)
        card.pack_start(self.devices_box, False, False, 2)
        self._sync()
        return card

    def _slid(self, value: float, final: bool) -> None:
        self.svc.set_volume("sink", value, source="slider")

    def _changed(self, kind: str, level, source: str = "") -> None:
        if kind == "source":
            self._mic_activity()
        if hasattr(self, "slider"):
            self._sync(skip_slider=source == "slider")

    def _sync(self, skip_slider: bool = False) -> None:
        sink, mic = self.svc.levels["sink"], self.svc.levels["source"]
        if not skip_slider:
            self.slider.set_value(min(sink.volume, 1.0))
        self.slider.icon = "volume-muted" if sink.muted or sink.volume == 0 else "volume"
        self.slider.set_dim(sink.muted)
        self.mic.set_icon("mic-off" if mic.muted else "mic")
        set_active(self.mic, not mic.muted)

    def _load_devices(self, devices) -> None:
        for child in self.devices_box.get_children():
            self.devices_box.remove(child)
        if len(devices) < 2:
            self.devices_box.hide()
            return
        for device in devices:
            button = chip(self.svc.friendly(device),
                          lambda d=device: self.svc.set_default("sink", d.id, self._refresh_devices))
            set_active(button, device.default)
            self.devices_box.add(button)
        self.devices_box.show_all()

    def _refresh_devices(self) -> None:
        self.svc.devices("sink", self._load_devices)

    def on_open(self) -> None:
        self.svc.refresh()
        self._refresh_devices()

    def commands(self) -> list[Command]:
        def volume(arg: str) -> str:
            arg = arg.strip().rstrip("%")
            if not arg:
                return f"Volume {round(self.svc.levels['sink'].volume * 100)}%"
            try:
                value = (self.svc.levels["sink"].volume * 100 + float(arg)) if arg[0] in "+-" else float(arg)
            except ValueError:
                return "Usage: v 0-100"
            result = self.svc.set_volume("sink", value / 100, "command")
            return f"Volume {round(result * 100)}%"

        return [
            Command(("v", "volume"), "Volume", "v 30 · v +5", volume, True),
            Command(("mute",), "Mute / unmute", "mute", lambda _a: self.toggle_mute("sink")),
            Command(("mic",), "Mute / unmute microphone", "mic", lambda _a: self.toggle_mute("source")),
        ]
