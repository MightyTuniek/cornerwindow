"""What the settings window shows: every option, with a label, an editor kind and help.

Anything in the config that is *not* described here (a new option, a user
module's own section) still appears: `infer_field` picks an editor from the
value's type. So every key is editable in the window as well as in the file.

Field kinds:
  bool · int · float · choice · text · color · action · list (comma separated)
  optional_int / optional_float (empty = use default/machine value)
  columns (module layout) · modules (one list of module ids) · presets · dict · limits · sensor
  layout (the Features page) · switchlist (reorderable on/off list) · place_search (city lookup)
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
    keys: tuple[str, ...] = ()  # edits several keys at once: the editor gets and emits {key: value}


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
    Section("modules", "Features", "settings", [
        F("columns", "Cards", "layout", "Switch a card off to hide it; it keeps its place. Drag the handle to "
          "move a card, or right-click it. Tools have no card: they add commands to the bar.",
          keys=("top", "columns", "drawer", "tools", "hidden")),
        F("search_paths", "Extra module folders", "list", "Folders with your own module .py files.", restart=True),
    ], "Choose what the panel shows, and where."),
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
        F("density", "Density", "choice", "Compact: tighter cards and smaller big numbers.",
          choices=["comfortable", "compact"]),
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
    Section("commandbar", "Command bar", "search", [
        F("always_show", "Always show the bar", "bool", "Off: it slides in when you start typing."),
        F("apps", "Find apps", "bool", "Type an app's name and press Enter to open it."),
        F("windows", "Switch to open windows", "bool", "Typing an app's name or a window title also "
          "offers its open windows."),
        F("path_programs", "Offer programs from PATH", "bool", "Typing “htop” offers to run it in a terminal."),
        F("terminal", "Terminal", "text", "For “>> command”: kitty, konsole, gnome-terminal… Empty = the "
          "first one installed."),
        F("search_url", "Web search", "text", "{} is replaced by what you typed. Empty = no web row."),
        F("engines", "Search shortcuts", "dict", "Shortcut → search URL. “yt lofi” searches YouTube."),
        F("currency_url", "Exchange rates", "text", "Fetched only when you type a currency conversion, then "
          "cached for 12 hours. Empty = off."),
    ], "Start typing anywhere in the open panel, or press ? in the bar to see everything it can do."),
    Section("display", "Display", "brightness", [
        F("limits", "Maximum", "limits", "Nothing can go above this: sliders, presets, scrolling, commands."),
        F("night_chip", "Night light chip", "choice", "Auto: only when the Night quick toggle isn't shown.",
          choices=["auto", "true", "false"]),
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
        F("names", "Output names", "dict", "Part of a device's name → what to call it."),
        F("input_names", "Microphone names", "dict", "The same for inputs."),
        F("mixer_ignore", "Hide from the app sliders", "list", "Part of an app's name."),
        F("backend", "Backend", "choice", choices=["auto", "wpctl", "pactl"], restart=True),
    ]),
    Section("media", "Now Playing", "music", [
        F("ignore", "Ignore players", "list", "Part of a player's name, e.g. kdeconnect or chromium."),
        F("prefer", "Prefer players", "list", "Shown first when nothing is playing, e.g. spotify."),
        F("trigger_dot", "Dot on the corner while playing", "bool"),
        F("mpd", "MPD", "choice", "Talk to MPD directly (not needed when mpDris2 runs).",
          choices=["auto", "false"], restart=True),
        F("mpd_host", "MPD host", "text", "Empty = $MPD_HOST or localhost.", restart=True),
        F("mpd_port", "MPD port", "optional_int", max=65535, restart=True),
    ], "Works with any MPRIS player and browser tabs; with several open, chips switch between them."),
    Section("toggles", "Toggles", "wifi", [
        F("items", "Toggles", "switchlist", "Drag to reorder; switched-off toggles aren't shown."),
        F("per_row", "Per row", "int", min=2, max=6),
        F("rows", "Rows", "int", "More toggles than fit go behind a “More” tile. 0 = no limit.", min=0, max=6),
        F("awake_minutes", "Awake for", "list", "Minutes offered by the Awake toggle's picker.", item_type=int),
    ]),
    Section("glance", "System", "monitor", [
        F("interval", "Update every", "float", min=0.25, max=10, step=0.25, digits=2, unit="s"),
        F("compact", "Start compact", "bool", "One row; click the card for sparklines, disk and processes."),
        F("processes", "Busiest processes", "int", "Listed in the full view, each with a stop button.",
          min=0, max=10),
        F("cpu_sensor", "CPU temperature", "sensor"),
        F("gpu_sensor", "GPU temperature", "sensor"),
        F("disk_path", "Disk", "text", "Which mount point to report free space for."),
    ]),
    Section("battery", "Battery", "battery", [
        F("low", "Warn below", "int", "A red dot on the corner while discharging.", min=1, max=50, unit="%"),
        F("peripherals", "Wireless devices", "choice", "Mice, keyboards, headsets, controllers, phones. "
          "Low: only when they need charging (the Bluetooth picker shows them all).",
          choices=["low", "always", "never"]),
        F("peripheral_low", "Low means", "int", "At or below this.", min=1, max=100, unit="%"),
    ], "Appears only when there is a battery to show."),
    Section("header", "Clock", "timer", [
        F("clock_format", "Time format", "text", "strftime codes, e.g. %H:%M or %I:%M %p."),
        F("date_format", "Date format", "text", "e.g. %A, %-d %B."),
        F("week_start", "Week starts on", "choice", "In the calendar (click the date to open it).",
          choices=["monday", "sunday", "saturday"]),
        F("next_event", "Next event under the date", "bool", "From your calendars (see Calendar)."),
        F("agenda", "Events under the calendar", "int", min=0, max=10),
        F("power", "Power button", "bool", "Lock, sleep, restart and shut down, when the Power card is off."),
    ]),
    Section("calendar", "Calendar", "calendar", [
        F("enabled", "Show events", "bool"),
        F("evolution", "GNOME Calendar and online accounts", "bool", "Read Evolution's local calendars and "
          "the copies it keeps of Google, Nextcloud and Exchange calendars."),
        F("sources", "Other calendars", "list", ".ics files or links, e.g. a Google calendar's secret address."),
        F("days", "Look ahead", "int", min=1, max=60, unit="days"),
        F("refresh_minutes", "Refresh every", "int", min=1, max=240, unit="min"),
    ], "Your next event under the clock, dots on busy days in the month calendar."),
    Section("notifications", "Notifications", "notification", [
        F("size", "Remember", "int", min=5, max=200, unit="items"),
        F("show", "Show", "int", "Listed before “N more”.", min=1, max=20),
        F("ignore", "Leave out", "list", "Part of an app's name."),
        F("trigger_dot", "Dot on the corner while unread", "bool"),
    ], "Notifications the desktop showed, kept for when you missed them. The card hides while empty."),
    Section("downloads", "Downloads", "download", [
        F("folder", "Folder", "text", "Empty = your downloads folder.", restart=True),
        F("keep_minutes", "Keep finished ones for", "int", min=1, max=1440, unit="min"),
        F("show", "Finished downloads shown", "int", min=1, max=10),
    ], "The card hides while nothing is downloading or just finished."),
    Section("updates", "Updates", "update", [
        F("check_hours", "Check every", "float", "When the panel opens, at most this often.", min=0.5, max=72,
          step=0.5, digits=1, unit="h"),
        F("flatpak", "Count Flatpak updates", "bool"),
        F("snap", "Count Snap updates", "bool"),
        F("trigger_dot", "Dot on the corner while updates wait", "bool"),
    ], "The card hides while everything is up to date."),
    Section("timer", "Timer", "timer", [
        F("presets", "Quick timers", "list", "Minutes.", item_type=int),
    ]),
    Section("drawer", "Drawer", "copy", [
        F("height", "Height", "int", min=80, max=500, step=10, unit="px"),
    ]),
    Section("clipboard", "Clipboard", "copy", [
        F("size", "Remember", "int", min=1, max=100, unit="items"),
        F("persist", "Keep after restart", "bool", "Stored in ~/.local/share/cornerwindow. Pins are always kept."),
        F("images", "Images", "int", "Copied images remembered (in memory only). 0 = text only.", min=0, max=20),
        F("clear_after", "Clear the clipboard after", "int", "For passwords: the system clipboard is emptied "
          "this long after you copy; the history keeps the entry. 0 = never.", min=0, max=3600, step=5, unit="s"),
    ]),
    Section("capture", "Capture", "screenshot", [
        F("copy", "Copy screenshots", "bool", "Also put them on the clipboard."),
        F("notify", "Notify when saved", "bool"),
        F("color_format", "Colours copy as", "choice", choices=["hex", "rgb", "hsl"]),
        F("ocr_language", "Text language", "text", "Tesseract's language codes, e.g. eng+deu. Empty = its default."),
        F("open_qr_links", "Open QR links", "bool", "They're always copied."),
        F("delay_ms", "Wait before capturing", "int", "Time for the panel to close.", min=0, max=3000, step=50,
          unit="ms"),
    ], "“shot”, “pick”, “ocr” and “qr” in the command bar. Text needs tesseract, QR codes zbar-tools."),
    Section("windowtools", "Snapping", "window", [
        F("gap", "Gap", "int", "Between snapped windows and around them.", max=60, unit="px"),
    ], "“snap left”, “snap tr”, “snap max”, “snap next” in the bar, or bind `cornerwindow action "
       "window.snap.left` to a keyboard shortcut. X11 only."),
    Section("snippets", "Snippets", "snippet", [
        F("items", "Snippets", "dict", "Name → text. Type ;name in the bar. {date} {time} {datetime} {weekday} "
          "{clipboard} are filled in; \\n is a new line."),
        F("paste", "Paste into the window", "bool", "Needs xdotool (X11) or wtype (Wayland); otherwise it's copied."),
        F("paste_delay_ms", "Paste delay", "int", min=0, max=2000, step=50, unit="ms"),
    ]),
    Section("shelf", "Shelf", "shelf", [
        F("size", "Keep", "int", min=1, max=100, unit="items"),
    ], "Drag files, text or links onto the corner, drop them on the Shelf tab, drag them out later."),
    Section("power", "Power", "poweroff", [
        F("items", "Buttons", "switchlist", "Drag to reorder; switched-off buttons aren't shown."),
        F("hold_ms", "Hold to confirm", "int", min=100, max=3000, step=50, unit="ms"),
        F("lock_hold_ms", "Hold to lock", "int", min=0, max=3000, step=50, unit="ms"),
    ]),
    Section("windows", "Apps", "apps", [
        F("show", "Show the Apps card", "choice", "Auto: only while the taskbar is hidden (Experimental).",
          choices=["auto", "always"]),
        F("favorites", "Show favourites", "bool", "The apps pinned to the desktop's dash or taskbar."),
        F("attention_dot", "Dot when a window needs attention", "bool", "Orange, on the corner."),
    ], "Click an app to switch to it, middle-click for a new window, right-click for its windows."),
    Section("alerts", "Alerts", "alert", [
        F("interval", "Check every", "int", min=5, max=600, unit="s"),
        F("cpu", "CPU busy above", "int", "0 = off.", max=100, unit="%"),
        F("temperature", "Temperature above", "int", "0 = off.", max=120, unit="°C"),
        F("memory", "Memory above", "int", "0 = off.", max=100, unit="%"),
        F("disk_gb", "Free disk below", "int", "0 = off.", max=1000, unit="GB"),
        F("sustain", "For at least", "int", "CPU, memory and temperature must stay high this long.",
          min=0, max=3600, step=10, unit="s"),
        F("cooldown", "Repeat after", "int", min=60, max=86400, step=60, unit="s"),
    ], "A tool that checks in the background (switch it on under Features)."),
    Section("agents", "AI agents", "agent", [
        F("interval", "Refresh every", "int", "While the panel is open.", min=2, max=120, unit="s"),
        F("window_budget", "Tokens per 5-hour window", "int",
          "Claude without its status line hook only: a meter of tokens against this. 0 = no meter.",
          max=100_000_000, step=100_000),
        F("trigger_dot", "Dot on the corner while an agent works", "bool", "Purple while one works, orange while one waits for you."),
        F("hide", "Leave out", "list", "Agents by id or name: claude, codex, opencode, gemini, copilot, amp, "
          "droid, pi, omp, grok, kimi, goose, cline, cursor, aider…"),
        F("herdr", "Ask herdr", "bool", "Which of herdr's agents work or wait for you, while herdr runs."),
        F("processes", "Find agents by their process", "bool",
          "For agents without usable logs (Cursor CLI, Aider, Kiro, Crush…): open, and working while busy."),
    ], "Claude Code, Codex, OpenCode, Gemini CLI and about twenty more, read from their logs on this PC. "
        "Run `cornerwindow agent setup` in a terminal to connect their hooks and Claude's real limits."),
    Section("weather", "Weather", "weather", [
        F("place", "City", "place_search", "Looked up on Open-Meteo when you press Find.",
          keys=("place", "latitude", "longitude")),
        F("latitude", "Latitude", "optional_float", min=-90, max=90, step=0.01, digits=4),
        F("longitude", "Longitude", "optional_float", min=-180, max=180, step=0.01, digits=4),
        F("forecast", "Forecast", "choice", choices=["hours", "days", "both", "none"]),
        F("units", "Units", "choice", choices=["metric", "imperial"]),
        F("refresh_minutes", "Refresh every", "int", min=5, max=240, step=5, unit="min"),
    ]),
    Section("experimental", "Experimental", "experimental", [
        F("hide_taskbar", "Hide the taskbar", "bool", "Works with Zorin Taskbar, Dash to Panel, Dash to Dock and "
          "Ubuntu Dock. Switch apps with the Apps card, the command bar, or Alt+Tab. The taskbar comes back "
          "when CornerWindow quits; `cornerwindow taskbar show` restores it by hand. Its tray icons and "
          "clock go with it."),
    ], "Features that aren't finished yet."),
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
