"""Battery: the laptop's own (percent, time left, charging) and every wireless device
UPower knows (mouse, keyboard, headset, controller, phone). Hidden while there are none."""

from __future__ import annotations

from gi.repository import Gtk

from ..core.activity import RED
from ..platform import sensors
from ..ui.draw import pill
from ..ui.widgets import Card, label
from ..ui.widgets.card import resolve_icon
from .base import Command, Module, module

DEVICE_ICONS = {
    "mouse": "mouse", "keyboard": "keyboard", "phone": "phone", "tablet": "tablet", "gamepad": "gamepad",
    "headset": "headphones", "headphones": "headphones", "speakers": "speaker", "audio": "speaker",
    "pen": "tablet", "touchpad": "mouse",
}


def battery_icon(percent: float, charging: bool) -> str:
    if charging:
        return "battery-charging"
    if percent >= 90:
        return "battery-full"
    if percent >= 40:
        return "battery-good"
    if percent >= 10:
        return "battery-low"
    return "battery-empty"


@module("battery", title="Battery", icon="battery",
        description="Laptop battery and wireless devices (mouse, headset, controller)")
class Battery(Module):
    def setup(self) -> None:
        self.svc = self.ctx.services.battery
        self.devices = self.svc.current() if self.svc.running else []
        self._low = False
        self.ctx.bus.subscribe("battery.changed", self._changed)
        self._update_low()

    def available(self) -> bool:
        return self.svc.running

    def build(self):
        self.revealer = Gtk.Revealer()
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.revealer.set_transition_duration(240)
        self.card = Card("Battery", spacing=6)
        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.card.pack_start(self.rows, False, False, 0)
        self.revealer.add(self.card)
        self._render()
        return self.revealer

    def _changed(self, devices) -> None:
        self.devices = devices
        self._update_low()
        if hasattr(self, "rows"):
            self._render()

    def _update_low(self) -> None:
        low_at = float(self.config.get("low", 15))
        laptop = next((d for d in self.devices if d.is_laptop), None)
        low = bool(laptop and not laptop.charging and laptop.percent <= low_at)
        if low != self._low:
            self._low = low
            if low:
                self.ctx.activity.show("battery.low", dot=True, color=RED, persistent=True, priority=4)
            else:
                self.ctx.activity.clear("battery.low")

    def _render(self) -> None:
        for child in self.rows.get_children():
            self.rows.remove(child)
            child.destroy()
        devices = shown_devices(self.devices, self.config.get("peripherals", "low"),
                                float(self.config.get("peripheral_low", 20)))
        for device in devices:
            self.rows.pack_start(self._laptop_row(device) if device.is_laptop else self._device_row(device),
                                 False, False, 0)
        self.rows.show_all()
        self.revealer.set_reveal_child(bool(devices))

    def _laptop_row(self, d) -> Gtk.Widget:
        box = Gtk.Box(spacing=10)
        image = Gtk.Image.new_from_icon_name(resolve_icon(battery_icon(d.percent, d.charging)), Gtk.IconSize.MENU)
        image.set_pixel_size(26)
        box.pack_start(image, False, False, 0)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        text.pack_start(label(f"{round(d.percent)}%", "cw-metric-value"), False, False, 0)
        text.pack_start(label(_status(d), "cw-dim cw-small"), False, False, 0)
        box.pack_start(text, True, True, 0)
        return box

    def _device_row(self, d) -> Gtk.Widget:
        box = Gtk.Box(spacing=8)
        image = Gtk.Image.new_from_icon_name(resolve_icon(DEVICE_ICONS.get(d.kind, "device")), Gtk.IconSize.MENU)
        image.set_pixel_size(16)
        box.pack_start(image, False, False, 0)
        box.pack_start(label(d.model, "cw-small", ellipsize=True), True, True, 0)
        bar = Gtk.DrawingArea()
        bar.set_size_request(54, 6)
        bar.set_valign(Gtk.Align.CENTER)
        bar.connect("draw", lambda w, cr, d=d: _draw_level(w, cr, d.percent))
        box.pack_start(bar, False, False, 0)
        box.pack_start(label(f"{round(d.percent)}%", "cw-dim cw-small cw-tnum"), False, False, 0)
        return box

    def commands(self) -> list[Command]:
        def run(_a: str) -> str:
            if not self.devices:
                return "No batteries found"
            return " · ".join(f"{'Battery' if d.is_laptop else d.model} {round(d.percent)}%" for d in self.devices)

        return [Command(("battery", "bat"), "Battery levels", "battery", run)]


def shown_devices(devices: list, peripherals, low: float) -> list:
    """The laptop's battery (and a UPS) always; wireless devices by `peripherals`:
    "always", "low" (only those at or below `low` %, the Bluetooth picker shows the rest) or "never".
    True / False mean always / never (older configs)."""
    mode = {True: "always", False: "never"}.get(peripherals, str(peripherals))
    out = []
    for d in devices:
        if d.is_laptop or d.kind == "ups":
            out.append(d)
        elif d.kind != "battery" and (mode == "always" or (mode == "low" and d.percent <= low)):
            out.append(d)
    return out


def _status(d) -> str:
    if d.state == "full":
        return "Fully charged"
    if d.charging:
        return f"Charging · full in {sensors.human_duration(d.time_to_full)}" if d.time_to_full else "Charging"
    if d.time_to_empty:
        return f"{sensors.human_duration(d.time_to_empty)} left"
    return "On battery" if d.state == "discharging" else d.state.replace("-", " ").capitalize()


def _draw_level(widget, cr, percent: float) -> bool:
    from ..ui import theme

    p = theme.current()
    w, h = widget.get_allocated_width(), widget.get_allocated_height()
    pill(cr, 0, 0, w, h)
    cr.set_source_rgba(*p.track)
    cr.fill()
    frac = max(0.0, min(1.0, percent / 100))
    pill(cr, 0, 0, max(h, w * frac), h)
    if percent <= 15:
        cr.set_source_rgba(*RED, 1)
    else:
        cr.set_source_rgba(*p.text[:3], 0.85)
    cr.fill()
    return True
