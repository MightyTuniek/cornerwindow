"""Monitor brightness & contrast (DDC/CI or backlight), presets and night light."""

from __future__ import annotations

import time

from gi.repository import Gtk

from ..core.activity import ORANGE, YELLOW
from ..platform.display.base import BRIGHTNESS, CONTRAST
from ..ui.widgets import Card, PillSlider, chip, label
from ..ui.widgets.card import set_active
from .base import Command, Module, module

SAVE_HOLD = 0.6


@module("display", title="Display", icon="brightness", description="Brightness and contrast, presets, night light")
class Display(Module):
    def setup(self) -> None:
        self.svc = self.ctx.services.display
        self.night = self.ctx.services.nightlight
        self.sliders: dict[str, PillSlider] = {}
        self.preset_chips: dict[str, Gtk.Button] = {}
        self.ctx.actions.register("display.brightness.step",
                                  lambda delta=1: self.nudge(BRIGHTNESS, delta), "Brightness up/down")
        self.ctx.actions.register("display.contrast.step",
                                  lambda delta=1: self.nudge(CONTRAST, delta), "Contrast up/down")
        self.ctx.actions.register("display.nightlight.toggle", self.toggle_night, "Toggle night light")
        self.ctx.bus.subscribe("display.changed", self._changed)
        self.ctx.bus.subscribe("display.monitor_changed", self._monitor_changed)
        self.monitor_sliders: dict[str, PillSlider] = {}
        self.ctx.bus.subscribe("display.ready", self._ready)
        if self.night.available:
            self.night.connect(self._night_changed)

    def configure(self) -> None:
        self.step = float(self.config.get("step", 5))
        for preset in self.presets():
            self.ctx.actions.register(f"display.preset.{preset['name'].lower()}",
                                      lambda p=preset: self.apply_preset(p), f"Preset {preset['name']}")

    # -- behaviour ------------------------------------------------------------------------
    def presets(self) -> list[dict]:
        saved = self.ctx.state.get("display.presets", {})
        out = []
        for preset in self.config.get("presets", []):
            merged = dict(preset)
            merged.update(saved.get(preset["name"], {}))
            out.append(merged)
        return out

    def nudge(self, control: str, delta: float) -> None:
        value = self.svc.step(control, delta * self.step, source="nudge")
        if value is None:
            self.ctx.activity.show("display.missing", dot=True, color=ORANGE, timeout=1.0)
            return
        self.ctx.activity.show(f"display.{control}", level=self._frac(control, value), label=f"{round(value)}",
                               color=YELLOW if control == BRIGHTNESS else (1, 1, 1))

    def _frac(self, control: str, percent: float) -> float:
        """Percent -> slider/ring position, where the configured limit is the full track."""
        return max(0.0, min(1.0, percent / self.svc.limit(control)))

    def apply_preset(self, preset: dict) -> str:
        if "brightness" in preset and self.svc.supports(BRIGHTNESS):
            self.svc.set_percent(BRIGHTNESS, preset["brightness"], source="preset")
        if "contrast" in preset and self.svc.supports(CONTRAST):
            self.svc.set_percent(CONTRAST, preset["contrast"], source="preset")
        if "night_light" in preset and self.night.available:
            self.night.set(bool(preset["night_light"]))
        if "warmth" in preset and self.night.available:
            self.night.set_warmth(float(preset["warmth"]) / 100)
        self._mark_presets()
        return f"{preset['name']} preset"

    def save_preset(self, name: str) -> None:
        saved = self.ctx.state.get("display.presets", {})
        values = {}
        for control in (BRIGHTNESS, CONTRAST):
            current = self.svc.percent(control)
            if current is not None:
                values[control] = round(current)
        if self.night.available:
            values["night_light"] = self.night.is_on()
        saved[name] = values
        self.ctx.state.set("display.presets", saved)
        self.ctx.activity.show("display.saved", dot=True, color=(0.19, 0.82, 0.35), timeout=1.0)
        self._mark_presets()

    def toggle_night(self) -> str:
        if not self.night.available:
            return "Night light is not supported here"
        on = not self.night.is_on()
        self.night.set(on)
        self.ctx.activity.show("display.night", dot=True, color=ORANGE if on else (1, 1, 1), timeout=1.2)
        return f"Night light {'on' if on else 'off'}"

    # -- UI -------------------------------------------------------------------------------
    def build(self):
        self.sliders, self.preset_chips, self.monitor_sliders = {}, {}, {}
        card = Card("Display")
        status_row = Gtk.Box(spacing=6)
        self.status = label("Looking for monitors…", "cw-dim cw-small")
        self.status.set_line_wrap(True)
        status_row.pack_start(self.status, True, True, 0)
        self.link_chip = chip("Linked", self.toggle_link, "Move every monitor together, or each on its own")
        self.link_chip.set_no_show_all(True)
        self.link_chip.set_valign(Gtk.Align.CENTER)
        status_row.pack_end(self.link_chip, False, False, 0)
        card.pack_start(status_row, False, False, 0)
        self.per_monitor = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.per_monitor.set_no_show_all(True)

        for control, icon in ((BRIGHTNESS, "sun"), (CONTRAST, "contrast")):
            # The slider spans 0..limit, so the whole track is the safe range.
            limit = self.svc.limit(control)
            slider = PillSlider(icon, lambda v, final, c=control: self._slid(c, v, final),
                                step=self.step / limit,
                                formatter=lambda v, lim=limit: f"{round(v * lim)}")
            slider.set_no_show_all(True)
            self.sliders[control] = slider
            card.pack_start(slider, False, False, 0)
            if control == BRIGHTNESS:
                card.pack_start(self.per_monitor, False, False, 0)

        row = Gtk.FlowBox()
        row.set_selection_mode(Gtk.SelectionMode.NONE)
        row.set_max_children_per_line(6)
        row.set_column_spacing(6)
        row.set_row_spacing(6)
        row.set_homogeneous(False)
        for preset in self.presets():
            button = chip(preset["name"], tooltip="Click to apply · hold to save current values")
            self._wire_preset(button, preset["name"])
            self.preset_chips[preset["name"]] = button
            row.add(button)
        if self.night.available and self._night_chip_wanted():
            self.night_chip = chip("Night light", self.toggle_night)
            row.add(self.night_chip)
        else:
            self.night_chip = None
        card.pack_start(row, False, False, 2)

        self.warmth_revealer = Gtk.Revealer()
        self.warmth_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.warmth_revealer.set_transition_duration(220)
        if self.night.available:
            warmth = PillSlider("night", lambda v, final: self.night.set_warmth(v), self.night.warmth(),
                                height=30, formatter=lambda v: "warm" if v > 0.66 else "mild" if v > 0.33 else "soft")
            self.warmth_revealer.add(warmth)
            self.warmth_slider = warmth
        card.pack_start(self.warmth_revealer, False, False, 0)

        if self.svc.ready.is_set():
            self._ready()
        self._night_changed()
        return card

    def _night_chip_wanted(self) -> bool:
        """The chip repeats the Night quick toggle: show it only when that toggle isn't on screen
        (night_chip = "auto"), or as configured (true / false)."""
        wanted = self.config.get("night_chip", "auto")
        if wanted != "auto":
            return str(wanted).lower() not in ("false", "0", "no", "off", "")
        cfg = self.ctx.config
        hidden = set(cfg.get("modules.hidden", []) or [])
        placed = list(cfg.get("modules.top", []) or [])
        for column in cfg.get("modules.columns", []) or []:
            placed += list(column)
        toggles_shown = "toggles" in placed and "toggles" not in hidden
        return not (toggles_shown and "night_light" in (cfg.get("toggles.items", []) or []))

    def _wire_preset(self, button: Gtk.Button, name: str) -> None:
        pressed = {"at": 0.0}

        def down(_b, event) -> bool:
            pressed["at"] = time.monotonic()
            return False

        def up(_b, event) -> bool:
            held = time.monotonic() - pressed["at"]
            if held >= SAVE_HOLD:
                self.save_preset(name)
            else:
                preset = next((p for p in self.presets() if p["name"] == name), None)
                if preset:
                    self.apply_preset(preset)
            return False

        button.connect("button-press-event", down)
        button.connect("button-release-event", up)

    def _slid(self, control: str, value: float, final: bool) -> None:
        self.svc.set_percent(control, value * self.svc.limit(control), source="slider")
        if final:
            self._mark_presets()

    # -- one slider per monitor -----------------------------------------------------------
    def linked(self) -> bool:
        return bool(self.ctx.state.get("display.linked", True))

    def toggle_link(self) -> str:
        self.ctx.state.set("display.linked", not self.linked())
        self._ready()
        return "Monitors move together" if self.linked() else "Each monitor on its own"

    def _build_monitor_sliders(self) -> None:
        for child in self.per_monitor.get_children():
            self.per_monitor.remove(child)
            child.destroy()
        self.monitor_sliders = {}
        limit = self.svc.limit(BRIGHTNESS)
        for monitor in self.svc.monitors_for(BRIGHTNESS):
            slider = PillSlider("sun", lambda v, final, m=monitor.id: self._slid_monitor(m, v, final),
                                step=self.step / limit,
                                formatter=lambda v, lim=limit, n=monitor.name: f"{n}  {round(v * lim)}")
            value = self.svc.percent(BRIGHTNESS, monitor.id)
            if value is not None:
                slider.set_value(self._frac(BRIGHTNESS, value), animate=False)
            self.monitor_sliders[monitor.id] = slider
            self.per_monitor.pack_start(slider, False, False, 0)
            slider.show()

    def _slid_monitor(self, monitor: str, value: float, final: bool) -> None:
        self.svc.set_percent(BRIGHTNESS, value * self.svc.limit(BRIGHTNESS), source="slider", monitor=monitor)
        if final:
            self._mark_presets()

    def _monitor_changed(self, control: str, monitor: str, percent: float, source: str = "") -> None:
        slider = self.monitor_sliders.get(monitor)
        if control == BRIGHTNESS and slider is not None and source != "slider":
            slider.set_value(self._frac(control, percent))

    def _ready(self) -> None:
        if not hasattr(self, "status"):
            return
        several = len(self.svc.monitors_for(BRIGHTNESS)) > 1
        split = several and not self.linked()
        self.link_chip.set_visible(several)
        self.link_chip.set_label("Linked" if self.linked() else "Separate")
        set_active(self.link_chip, self.linked())
        if split:
            self._build_monitor_sliders()
        self.per_monitor.set_visible(split)
        any_control = False
        for control, slider in self.sliders.items():
            supported = self.svc.supports(control)
            slider.set_visible(supported and not (split and control == BRIGHTNESS))
            if supported:
                any_control = True
                slider.set_value(self._frac(control, self.svc.percent(control)), animate=False)
        if any_control:
            names = ", ".join(m.name for m in self.svc.monitors)
            self.status.set_text(names)
        else:
            self.status.set_text("No controllable monitor found. External monitors need DDC/CI "
                                 "enabled in their menu and I²C access (run install.sh, then "
                                 "`cornerwindow doctor`).")
        self._mark_presets()

    def _changed(self, control: str, percent: float, source: str = "") -> None:
        slider = self.sliders.get(control)
        if slider and source != "slider":
            slider.set_value(self._frac(control, percent))
        if source not in ("slider",):
            self._mark_presets()

    def _night_changed(self) -> None:
        on = self.night.is_on() if self.night.available else False
        if getattr(self, "night_chip", None) is not None:
            set_active(self.night_chip, on)
        if hasattr(self, "warmth_revealer"):
            self.warmth_revealer.set_reveal_child(on)
        self._mark_presets()

    def _mark_presets(self) -> None:
        b, c = self.svc.percent(BRIGHTNESS), self.svc.percent(CONTRAST)
        night = self.night.is_on() if self.night.available else None
        for preset in self.presets():
            button = self.preset_chips.get(preset["name"])
            if not button:
                continue
            match = b is not None and abs(b - preset.get("brightness", b)) < 1.5
            match = match and (c is None or abs(c - preset.get("contrast", c)) < 1.5)
            if night is not None and "night_light" in preset:
                match = match and bool(preset["night_light"]) == night
            set_active(button, match)

    def on_open(self) -> None:
        if self.svc.ready.is_set() and self.config.get("refresh_on_open", True):
            self.svc.refresh()

    def commands(self) -> list[Command]:
        def setter(control: str):
            def run(arg: str) -> str | None:
                arg, _, which = arg.strip().partition(" ")
                arg = arg.rstrip("%")
                monitor = None
                if which.strip():  # "b 40 2" or "b 40 dell": just that monitor
                    found = self.svc.find_monitor(which, control)
                    if found is None:
                        return f"No monitor matches {which.strip()!r}"
                    monitor = found.id
                if not arg:
                    value = self.svc.percent(control, monitor)
                    return f"{control.title()} is {round(value)}%" if value is not None else "Not available"
                try:
                    if arg[0] in "+-":
                        value = self.svc.step(control, float(arg), "command", monitor)
                    else:
                        value = self.svc.set_percent(control, float(arg), "command", monitor)
                except ValueError:
                    return f"Usage: {control[0]} 0-100"
                if value is None:
                    return "Not available"
                self.ctx.activity.show(f"display.{control}", level=self._frac(control, value), label=str(round(value)),
                                       color=YELLOW if control == BRIGHTNESS else (1, 1, 1))
                return f"{control.title()} {round(value)}%"
            return run

        cmds = [
            Command(("b", "brightness"), "Brightness", "b 40 · b +10 · b 40 2", setter(BRIGHTNESS), True),
            Command(("c", "contrast"), "Contrast", "c 60", setter(CONTRAST), True),
            Command(("night", "nightlight"), "Toggle night light", "night", lambda _a: self.toggle_night()),
        ]
        for preset in self.presets():
            cmds.append(Command((preset["name"].lower(),), f"{preset['name']} preset", preset["name"].lower(),
                                lambda _a, p=preset: self.apply_preset(p)))
        return cmds
