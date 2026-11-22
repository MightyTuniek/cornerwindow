"""What the settings window shows: every option, with a label, an editor kind and help.

Anything in the config that is *not* described here (a new option, a user
module's own section) still appears: `infer_field` picks an editor from the
value's type. So every key is editable in the window as well as in the file.

Field kinds:
  bool · int · float · choice · text · color · action · list (comma separated)
  optional_int / optional_float (empty = use default/machine value)
  columns (module layout) · modules (one list of module ids) · presets · dict · limits · sensor
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Field:
    key: str
    label: str
    kind: str
    help: str = ""
    min: float = 0
    max: float = 100
    step: float = 1
    digits: int = 0
    unit: str = ""
    choices: list[str] = field(default_factory=list)
    item_type: type = str  # for kind="list"
    restart: bool = False  # only takes effect after restarting the app


@dataclass
class Section:
    id: str
    title: str
    icon: str
    fields: list[Field]
    help: str = ""


def F(key: str, label: str, kind: str, help: str = "", **kw: Any) -> Field:
    return Field(key, label, kind, help, **kw)


SPRING = "Spring response in seconds (higher = slower)."
DAMP = "Damping: 1 = no bounce, lower = more bounce."

SECTIONS: list[Section] = [
    Section("general", "General", "settings", [
        F("corner", "Corner", "choice", choices=["top-right", "top-left", "bottom-right", "bottom-left"]),
        F("monitor", "Monitor", "text", "“primary”, a monitor number (0, 1…) or part of its model name."),
        F("open_on", "Open on", "choice", choices=["click", "hover"]),
        F("hover_open_delay_ms", "Hover delay", "int", "Only used when opening on hover.", max=2000, step=50, unit="ms"),
        F("close_on_focus_out", "Close when clicking elsewhere", "bool"),
        F("close_on_leave_ms", "Close after pointer leaves", "int", "0 = never.", max=10000, step=100, unit="ms"),
        F("hide_when_fullscreen", "Hide during fullscreen apps", "bool"),
        F("always_on_top", "Always on top, even over fullscreen", "bool"),
        F("window_type", "Window type (X11)", "choice", "Try “dock” if your window manager misbehaves.",
          choices=["utility", "dock"], restart=True),
    ]),
    Section("trigger", "Trigger", "pin", [
        F("radius", "Size at rest", "int", min=6, max=60, unit="px"),
        F("hover_radius", "Size on hover", "int", min=6, max=80, unit="px"),
        F("scroll", "Scroll", "action"),
        F("shift_scroll", "Shift + scroll", "action"),
        F("middle_click", "Middle click", "action"),
        F("right_click", "Right click", "action"),
    ], "What the collapsed quarter-circle does."),
    Section("panel", "Look", "monitor", [
        F("background", "Background", "color"),
        F("column_width", "Column width", "int", min=220, max=520, step=5, unit="px"),
        F("spacing", "Card spacing", "int", max=40, unit="px"),
        F("padding", "Edge padding", "int", max=48, unit="px"),
        F("corner_radius", "Corner radius", "int", max=80, unit="px"),
        F("ears", "Edge corners", "int", "Where the panel meets the screen edges: negative rounds the corner "
          "into the panel, positive flares it outward, 0 is square.", min=-40, max=40, unit="px"),
        F("smoothing", "Corner smoothing", "float", "0 = circular, 0.6 = iOS-style continuous.", max=1, step=0.05, digits=2),
        F("shadow", "Shadow", "bool"),
        F("glass", "Liquid Glass", "bool", "See-through glass that bends what is behind it. Tune it under Glass."),
    ]),
    Section("glass", "Glass", "glass", [
        F("appearance", "Appearance", "choice", "System follows the desktop's light/dark setting; "
          "adaptive picks by how bright the content behind is.", choices=["system", "light", "dark", "adaptive"]),
        F("frost", "Frost", "float", "0 = clear glass, 1 = frosted.", max=1, step=0.05, digits=2),
        F("tint", "Tint", "float", "More tint reads easier but shows less of what's behind.", max=1, step=0.05, digits=2),
        F("refraction", "Lensing", "float", "How strongly the rim bends the backdrop.", max=3, step=0.05, digits=2),
        F("dispersion", "Colour fringe", "float", "Rainbow split where the light bends most.", max=1, step=0.05, digits=2),
        F("light_angle", "Light direction", "int", "0 = from the top, -90 = from the left, 90 = from the right.",
          min=-180, max=180, step=5, unit="°"),
        F("live", "Live backdrop", "bool", "X11: follow what moves behind the glass. Off = wallpaper only.", restart=True),
    ], "Only used when Liquid Glass is on (Look)."),
    Section("animation", "Motion", "timer", [
        F("open_response", "Open speed", "float", SPRING, min=0.1, max=2, step=0.02, digits=2, unit="s"),
        F("open_damping", "Open bounce", "float", DAMP, min=0.3, max=1.2, step=0.02, digits=2),
        F("open_height_response", "Open speed (height)", "float", SPRING, min=0.1, max=2, step=0.02, digits=2, unit="s"),
        F("content_response", "Cards appear", "float", SPRING, min=0.1, max=2, step=0.02, digits=2, unit="s"),
        F("stagger", "Card stagger", "float", "Delay between cards pouring out.", max=0.5, step=0.01, digits=2),
        F("close_response", "Close speed", "float", SPRING, min=0.1, max=2, step=0.02, digits=2, unit="s"),
        F("close_damping", "Close bounce", "float", DAMP, min=0.3, max=1.2, step=0.02, digits=2),
        F("content_out_response", "Cards fade out", "float", SPRING, min=0.05, max=1, step=0.01, digits=2, unit="s"),
        F("hover_response", "Hover speed", "float", SPRING, min=0.05, max=1, step=0.01, digits=2, unit="s"),
        F("hover_damping", "Hover bounce", "float", DAMP, min=0.3, max=1.2, step=0.02, digits=2),
        F("slow_motion", "Slow motion", "float", "Above 1 slows every animation down, for tuning.",
          min=0.2, max=10, step=0.1, digits=1, unit="×"),
    ]),
    Section("modules", "Layout", "settings", [
        F("columns", "Columns", "columns", "Cards per column, left to right, top to bottom."),
        F("top", "Above the columns", "modules"),
        F("drawer", "Drawer tabs", "modules"),
        F("search_paths", "Extra module folders", "list", "Folders with your own module .py files.", restart=True),
    ]),
    Section("display", "Display", "brightness", [
        F("limits", "Maximum", "limits", "Nothing can go above this: sliders, presets, scrolling, commands."),
        F("presets", "Presets", "presets", "Click a preset in the island to apply it; hold it there to save current values."),
        F("step", "Scroll step", "int", min=1, max=25, unit="%"),
        F("refresh_on_open", "Re-read values on open", "bool", "Picks up changes made with the monitor's buttons."),
        F("backend", "Backend", "list", "In order of preference: ddc, ddcutil, backlight, gamma — or auto.", restart=True),
        F("i2c_bus", "I²C bus", "optional_int", "Empty = detect automatically.", max=64, restart=True),
    ]),
    Section("audio", "Sound", "volume", [
        F("step", "Scroll step", "int", min=1, max=25, unit="%"),
        F("max_volume", "Maximum volume", "int", "Above 100 boosts.", min=10, max=200, step=5, unit="%"),
        F("show_mic_muted", "Red dot when mic is muted", "bool"),
        F("names", "Device names", "dict", "Part of a device's name → what to call it."),
        F("backend", "Backend", "choice", choices=["auto", "wpctl", "pactl"], restart=True),
    ]),
    Section("toggles", "Toggles", "wifi", [
        F("items", "Toggles", "list", "Shown in this order. Available: wifi, bluetooth, night_light, dark_mode, dnd, caffeine, mic, glass."),
        F("per_row", "Per row", "int", min=2, max=6),
    ]),
    Section("glance", "System", "monitor", [
        F("interval", "Update every", "float", min=0.25, max=10, step=0.25, digits=2, unit="s"),
        F("cpu_sensor", "CPU temperature", "sensor"),
        F("gpu_sensor", "GPU temperature", "sensor"),
        F("disk_path", "Disk", "text", "Which mount point to report free space for."),
    ]),
    Section("header", "Clock", "timer", [
        F("clock_format", "Time format", "text", "strftime codes, e.g. %H:%M or %I:%M %p."),
        F("date_format", "Date format", "text", "e.g. %A, %-d %B."),
    ]),
    Section("timer", "Timer", "timer", [
        F("presets", "Quick timers", "list", "Minutes.", item_type=int),
    ]),
    Section("drawer", "Drawer", "copy", [
        F("height", "Height", "int", min=80, max=500, step=10, unit="px"),
    ]),
    Section("clipboard", "Clipboard", "copy", [
        F("size", "Remember", "int", min=1, max=100, unit="items"),
        F("persist", "Keep after restart", "bool", "Stored in ~/.local/share/cornerwindow."),
    ]),
    Section("power", "Power", "poweroff", [
        F("items", "Buttons", "list", "Available: lock, suspend, logout, reboot, poweroff."),
        F("hold_ms", "Hold to confirm", "int", min=100, max=3000, step=50, unit="ms"),
        F("lock_hold_ms", "Hold to lock", "int", min=0, max=3000, step=50, unit="ms"),
    ]),
    Section("weather", "Weather", "weather", [
        F("latitude", "Latitude", "optional_float", "Add “weather” to a column in Layout to show the card.",
          min=-90, max=90, step=0.01, digits=4),
        F("longitude", "Longitude", "optional_float", min=-180, max=180, step=0.01, digits=4),
        F("place", "Place name", "text"),
        F("units", "Units", "choice", choices=["metric", "imperial"]),
        F("refresh_minutes", "Refresh every", "int", min=5, max=240, step=5, unit="min"),
    ]),
]

SKIP_SECTIONS = {"match"}


def section_for(section_id: str) -> Section | None:
    return next((s for s in SECTIONS if s.id == section_id), None)


def infer_field(key: str, value: Any) -> Field:
    """An editor for an option the schema doesn't know about."""
    label = key.replace("_", " ").capitalize()
    if isinstance(value, bool):
        return F(key, label, "bool")
    if isinstance(value, int):
        return F(key, label, "int", min=-1_000_000, max=1_000_000)
    if isinstance(value, float):
        return F(key, label, "float", min=-1_000_000, max=1_000_000, step=0.1, digits=2)
    if isinstance(value, list) and all(isinstance(v, (str, int, float)) for v in value):
        item = type(value[0]) if value else str
        return F(key, label, "list", item_type=item)
    if isinstance(value, dict) and all(isinstance(v, (str, int, float, bool)) for v in value.values()):
        return F(key, label, "dict")
    if isinstance(value, (dict, list)):
        return F(key, label, "toml", "Edited as TOML.")
    return F(key, label, "text")
