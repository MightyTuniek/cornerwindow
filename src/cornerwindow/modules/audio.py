"""Output volume, mute, output and input devices, microphone level, per-app volume."""

from __future__ import annotations

from gi.repository import Gio, Gtk, Pango

from ..core.activity import RED, WHITE
from ..ui.widgets import Card, IconButton, PillSlider, chip, label
from ..ui.widgets.card import resolve_icon, set_active
from .base import Command, Module, module


@module("audio", title="Sound", icon="volume", description="Volume, output and input devices, per-app volume")
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

    @property
    def span(self) -> float:
        """The slider's full track: 1.0 = 100 %, more when boosting (max_volume = 150)."""
        return max(1.0, self.svc.max_volume)

    def _pos(self, volume: float) -> float:
        return max(0.0, min(1.0, volume / self.span))

    def _fmt(self, value: float) -> str:
        return f"{round(value * self.span * 100)}"

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
        self.slider = PillSlider("volume", self._slid, self._pos(self.svc.levels["sink"].volume),
                                 step=self.step / 100 / self.span, formatter=self._fmt)
        row.pack_start(self.slider, True, True, 0)
        self.mic = IconButton("mic", lambda: self.toggle_mute("source"), "Microphone", "cw-icon cw-big")
        row.pack_start(self.mic, False, False, 0)
        card.pack_start(row, False, False, 0)
        self.outputs = DevicePicker("headphones", lambda d: self._pick_device("sink", d))
        card.pack_start(self.outputs, False, False, 0)

        # "Microphone & apps": mic level, input devices, one slider per app
        toggle = Gtk.Button()
        toggle.set_relief(Gtk.ReliefStyle.NONE)
        toggle.set_can_focus(False)
        toggle.get_style_context().add_class("cw-disclosure")
        line = Gtk.Box(spacing=4)
        line.pack_start(label("Microphone & apps", "cw-dim cw-small"), False, False, 0)
        self.chevron = Gtk.Image.new_from_icon_name(resolve_icon("chevron"), Gtk.IconSize.MENU)
        self.chevron.set_pixel_size(12)
        line.pack_start(self.chevron, False, False, 0)
        toggle.add(line)
        toggle.set_halign(Gtk.Align.START)
        toggle.connect("clicked", lambda *_: self._set_more(not self.more.get_reveal_child()))
        card.pack_start(toggle, False, False, 0)
        self.more = Gtk.Revealer()
        self.more.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.more.set_transition_duration(220)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.mic_slider = PillSlider("mic", self._mic_slid, self.svc.levels["source"].volume, height=30,
                                     step=self.step / 100)
        inner.pack_start(self.mic_slider, False, False, 0)
        self.inputs = DevicePicker("mic", lambda d: self._pick_device("source", d))
        inner.pack_start(self.inputs, False, False, 0)
        self.apps_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        inner.pack_start(self.apps_box, False, False, 0)
        self.more.add(inner)
        card.pack_start(self.more, False, False, 0)
        self._app_rows: dict[str, dict] = {}
        self.more.set_reveal_child(bool(self.ctx.state.get("audio.more", False)))
        self._update_chevron()
        self.ctx.every(2.0, self._refresh_apps, immediately=False)
        self._sync()
        return card

    def _set_more(self, show: bool) -> None:
        self.more.set_reveal_child(show)
        self.ctx.state.set("audio.more", show)
        self._update_chevron()
        if show:
            self._refresh_inputs()
            self._refresh_apps()

    def _update_chevron(self) -> None:
        self.chevron.set_from_icon_name(resolve_icon("chevron-up" if self.more.get_reveal_child() else "chevron"),
                                        Gtk.IconSize.MENU)

    def _mic_slid(self, value: float, final: bool) -> None:
        self.svc.set_volume("source", value, source="mic-slider")

    def _slid(self, value: float, final: bool) -> None:
        self.svc.set_volume("sink", value * self.span, source="slider")

    def _changed(self, kind: str, level, source: str = "") -> None:
        if kind == "source":
            self._mic_activity()
        if hasattr(self, "slider"):
            self._sync(skip_slider=source == "slider", skip_mic=source == "mic-slider")

    def _sync(self, skip_slider: bool = False, skip_mic: bool = False) -> None:
        sink, mic = self.svc.levels["sink"], self.svc.levels["source"]
        if not skip_slider:
            self.slider.set_value(self._pos(sink.volume))
        if not skip_mic:
            self.mic_slider.set_value(min(mic.volume, 1.0))
        self.mic_slider.icon = "mic-off" if mic.muted else "mic"
        self.mic_slider.set_dim(mic.muted)
        self.slider.icon = "volume-muted" if sink.muted or sink.volume == 0 else "volume"
        self.slider.set_dim(sink.muted)
        self.mic.set_icon("mic-off" if mic.muted else "mic")
        set_active(self.mic, not mic.muted)

    def _load_devices(self, devices, kind: str = "sink") -> None:
        if kind == "sink":
            self.sinks = devices
        picker = self.outputs if kind == "sink" else self.inputs
        picker.load([(d, self.svc.friendly(d, kind)) for d in devices])

    def _pick_device(self, kind: str, device) -> None:
        refresh = self._refresh_devices if kind == "sink" else self._refresh_inputs
        self.svc.set_default(kind, device.id, refresh)

    def _refresh_devices(self) -> None:
        self.svc.devices("sink", self._load_devices)

    def _refresh_inputs(self) -> None:
        self.svc.devices("source", lambda devices: self._load_devices(devices, "source"))

    # -- per-app volume -----------------------------------------------------------------
    def _refresh_apps(self) -> None:
        if hasattr(self, "more") and self.more.get_reveal_child():
            self.svc.apps(self._load_apps)

    def _load_apps(self, apps) -> None:
        if not hasattr(self, "apps_box"):
            return
        names = [a.app for a in apps]
        if names != list(self._app_rows):
            for child in self.apps_box.get_children():
                self.apps_box.remove(child)
                child.destroy()
            self._app_rows = {}
            for app in apps:
                self._app_rows[app.app] = self._app_row(app)
            if not apps:
                self.apps_box.pack_start(label("No app is playing sound", "cw-dim cw-small"), False, False, 0)
            self.apps_box.show_all()
        for app in apps:
            row = self._app_rows[app.app]
            row["app"] = app
            if row["slider"]._drag is None:
                row["slider"].set_value(self._pos(app.volume))
            row["slider"].set_dim(app.muted)
            row["mute"].set_icon("volume-muted" if app.muted else "volume")

    def _app_row(self, app) -> dict:
        row: dict = {"app": app}
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box = Gtk.Box(spacing=6)
        box.set_tooltip_text(app.app)
        image = _app_image(app.icon or app.app)
        box.pack_start(image, False, False, 0)
        slider = PillSlider("volume", lambda v, _f, r=row: self.svc.set_app_volume(r["app"], v * self.span),
                            self._pos(app.volume), height=26, step=self.step / 100 / self.span,
                            formatter=lambda v, name=app.app: f"{name}  {round(v * self.span * 100)}")
        box.pack_start(slider, True, True, 0)
        route = IconButton("headphones", lambda r=row: self._toggle_routes(r), "Play this app on…",
                           "cw-icon cw-plain", 14)
        route.set_no_show_all(len(getattr(self, "sinks", [])) < 2)
        box.pack_start(route, False, False, 0)
        mute = IconButton("volume", lambda r=row: self._toggle_app_mute(r), "Mute this app", "cw-icon cw-plain", 14)
        box.pack_start(mute, False, False, 0)
        outer.pack_start(box, False, False, 0)
        routes = Gtk.Revealer()
        routes.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        routes.set_transition_duration(180)
        routes.add(_chip_box(visible=True))
        outer.pack_start(routes, False, False, 0)
        row.update(slider=slider, mute=mute, routes=routes, widget=outer)
        self.apps_box.pack_start(outer, False, False, 0)
        return row

    def _toggle_routes(self, row: dict) -> None:
        """Per-app output: chips for every output, the one the app plays on lit."""
        routes: Gtk.Revealer = row["routes"]
        if routes.get_reveal_child():
            routes.set_reveal_child(False)
            return
        box = routes.get_child()
        for child in box.get_children():
            box.remove(child)
            child.destroy()
        app = row["app"]
        for device in getattr(self, "sinks", []):
            button = chip(self.svc.friendly(device), lambda d=device, r=row: self._route(r, d))
            set_active(button, device.id == app.sink or (not app.sink and device.default))
            box.add(button)
        box.show_all()
        routes.set_reveal_child(True)

    def _route(self, row: dict, device) -> None:
        self.svc.move_app(row["app"], device.id)
        row["routes"].set_reveal_child(False)
        self.ctx.activity.show("audio.route", dot=True, color=WHITE, timeout=0.9)

    def _toggle_app_mute(self, row: dict) -> None:
        app = row["app"]
        self.svc.set_app_muted(app, not app.muted)
        row["slider"].set_dim(app.muted)
        row["mute"].set_icon("volume-muted" if app.muted else "volume")

    def on_open(self) -> None:
        self.svc.refresh()
        self._refresh_devices()
        if hasattr(self, "more") and self.more.get_reveal_child():
            self._refresh_inputs()
            self._refresh_apps()

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

        def output(arg: str) -> str:
            arg = arg.strip().lower()
            sinks = getattr(self, "sinks", [])
            if not arg:
                current = next((d for d in sinks if d.default), None)
                return f"Output: {self.svc.friendly(current)}" if current else "Usage: out headphones"
            device = next((d for d in sinks if arg in self.svc.friendly(d).lower() or arg in d.name.lower()), None)
            if device is None:
                return f"No output matches {arg!r}"
            self._pick_device("sink", device)
            return f"Output: {self.svc.friendly(device)}"

        return [
            Command(("v", "volume"), "Volume", "v 30 · v +5", volume, True),
            Command(("out", "output"), "Switch the sound output", "out headphones · out", output, True),
            Command(("mute",), "Mute / unmute", "mute", lambda _a: self.toggle_mute("sink")),
            Command(("mic",), "Mute / unmute microphone", "mic", lambda _a: self.toggle_mute("source")),
        ]


def _chip_box(visible: bool = False) -> Gtk.FlowBox:
    box = Gtk.FlowBox()
    box.set_selection_mode(Gtk.SelectionMode.NONE)
    box.set_column_spacing(6)
    box.set_row_spacing(6)
    box.set_max_children_per_line(4)
    box.set_no_show_all(not visible)
    return box


class DevicePicker(Gtk.Box):
    """One chip with the current device ("Sennheiser HD 660S ▾"); clicking it lists the others.
    Hidden while there is only one device, so a single-output PC shows nothing at all."""

    def __init__(self, icon: str, on_pick) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.on_pick = on_pick
        self.set_no_show_all(True)
        self.button = Gtk.Button()
        self.button.set_relief(Gtk.ReliefStyle.NONE)
        self.button.set_can_focus(False)
        self.button.get_style_context().add_class("cw-chip")
        self.button.set_halign(Gtk.Align.START)
        line = Gtk.Box(spacing=6)
        self.icon = Gtk.Image.new_from_icon_name(resolve_icon(icon), Gtk.IconSize.MENU)
        self.icon.set_pixel_size(14)
        line.pack_start(self.icon, False, False, 0)
        self.name = Gtk.Label(label="")
        self.name.set_ellipsize(Pango.EllipsizeMode.END)
        self.name.set_max_width_chars(26)
        line.pack_start(self.name, False, False, 0)
        self.chevron = Gtk.Image.new_from_icon_name(resolve_icon("chevron"), Gtk.IconSize.MENU)
        self.chevron.set_pixel_size(10)
        line.pack_start(self.chevron, False, False, 0)
        self.button.add(line)
        self.button.connect("clicked", lambda *_: self.set_open(not self.list.get_reveal_child()))
        self.pack_start(self.button, False, False, 0)
        self.list = Gtk.Revealer()
        self.list.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.list.set_transition_duration(180)
        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        self.list.add(self.rows)
        self.pack_start(self.list, False, False, 0)
        for child in (self.button, self.list):
            child.show_all()

    def load(self, devices: list) -> None:
        """devices: [(Device, friendly name)]."""
        for child in self.rows.get_children():
            self.rows.remove(child)
            child.destroy()
        if len(devices) < 2:
            self.hide()
            return
        current = next((name for d, name in devices if d.default), devices[0][1])
        self.name.set_text(current)
        for device, name in devices:
            row = Gtk.Button()
            row.set_relief(Gtk.ReliefStyle.NONE)
            row.set_can_focus(False)
            row.get_style_context().add_class("cw-row")
            set_active(row, device.default)
            row.add(label(name, "", ellipsize=True))
            row.connect("clicked", lambda _b, d=device: (self.set_open(False), self.on_pick(d)))
            self.rows.pack_start(row, False, False, 0)
        self.rows.show_all()
        self.show()

    def set_open(self, show: bool) -> None:
        self.list.set_reveal_child(show)
        self.chevron.set_from_icon_name(resolve_icon("chevron-up" if show else "chevron"), Gtk.IconSize.MENU)


_ICONS: dict[str, Gio.Icon | None] = {}


def _app_image(name: str) -> Gtk.Image:
    """The app's own icon (Spotify's logo for Spotify), else a speaker."""
    if name not in _ICONS:
        from ..platform.desktop import app_icon

        found = app_icon(name)
        icon = None
        if found:
            try:
                icon = Gio.Icon.new_for_string(found)
            except Exception:
                icon = None
        elif Gtk.IconTheme.get_default().has_icon(name):
            icon = Gio.ThemedIcon.new(name)
        _ICONS[name] = icon
    icon = _ICONS[name]
    image = Gtk.Image.new_from_gicon(icon, Gtk.IconSize.MENU) if icon else \
        Gtk.Image.new_from_icon_name(resolve_icon("speaker"), Gtk.IconSize.MENU)
    image.set_pixel_size(18)
    return image
