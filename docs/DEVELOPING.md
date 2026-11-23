# Developing CornerWindow

## Writing a module

A module is one file. Drop it into `~/.config/cornerwindow/modules/` (or `src/cornerwindow/modules/` for a built-in), then add its id to a column in `[modules]`.

```python
# ~/.config/cornerwindow/modules/uptime_card.py
from cornerwindow.modules.base import Command, Module, module
from cornerwindow.platform import sensors
from cornerwindow.ui.widgets import Card, label


@module("uptime_card", title="Uptime", icon="timer", description="How long this PC has been up")
class UptimeCard(Module):
    def build(self):
        card = Card("Uptime")
        self.text = label("", "cw-metric-value")
        card.pack_start(self.text, False, False, 0)
        self.ctx.every(30, self.refresh)  # runs only while the panel is open
        return card

    def refresh(self):
        self.text.set_text(sensors.human_duration(sensors.uptime()))

    def commands(self):  # optional: entries for the command bar
        return [Command(("up", "uptime"), "Uptime", "up", lambda _arg: self.text.get_text())]
```

### Tools: commands without a card

A module with `area="tool"` has no card: `build()` may return `None` (that's the default) and the module still counts as shown, so its commands, actions and `ctx.every()` timers work. Tools are listed in `[modules] tools` and switched on and off on the Features page like cards.

```python
@module("hello_tool", title="Hello", area="tool", icon="snippet", description="Says hello from the bar")
class HelloTool(Module):
    def commands(self):
        return [Command(("hi",), "Say hello", "hi", lambda _arg: "Hello!")]
```

### Command bar entries

`Command(keywords, title, usage, run, takes_arg)` is all a command needs; `run(arg)` returns the feedback text. It shows up in the bar's **?** list under the module's title automatically, with the first `usage` example (examples are separated by ` · `) as the text a click puts in the bar. Optional extras:

| Field | Purpose |
|---|---|
| `suggest=fn(arg)` | Live rows once the keyword is typed in full: return a list of `Suggestion(title, detail, run, icon, closes, complete)` (the clipboard's `cb` lists matching entries this way) |
| `confirm=True` | The row needs <kbd>Enter</kbd> twice (restart, shut down); a `Suggestion` takes `confirm=True` too (stopping a process) |
| `group="…"` | A different heading in the ? list |
| `icon="…"` | Row icon; defaults to the module's |

Pure parsing for the bar lives in `core/`: `calc.py` (maths, units, bases, currencies), `clock.py` (times, zones, dates), `cmdline.py` (shell lines, paths, URLs, search shortcuts, file ranking), `history.py` (recents and frecency) and `emoji.py`.

### Context

Everything a module needs is on `self.ctx`:

| Member | Purpose |
|---|---|
| `section` | This module's `[uptime_card]` config table |
| `services` | `display`, `audio`, `media`, `battery`, `nightlight`, `wifi`, `bluetooth`, `vpn`, `airplane`, `power_profiles`, `dark_mode`, `dnd`, `caffeine`, `clipboard`, `windows` (open windows by app, window snapping), `taskbar`, `notifications` (history), `downloads`, `calendar` |
| `actions.register(name, fn)` | Register a named action that config can bind to trigger gestures |
| `activity.show(id, level=..., label=..., dot=..., color=...)` | Show a live activity on the collapsed trigger |
| `bus.subscribe(topic, fn)` | Subscribe to events such as `display.changed`, `display.monitor_changed`, `audio.changed`, `media.changed`, `battery.changed`, `windows.changed`, `notifications.changed`, `downloads.changed`, `calendar.changed` |
| `every(sec, fn)` | Call `fn` every `sec` seconds while the panel is open |
| `run_async(fn, then)` | Run `fn` off the main thread, then call `then` with the result |
| `notify(title, body)` | Show a desktop notification |

Also available: `state`, `data_dir` and `island` (the window).

### Hooks

Override any of these on your `Module` subclass:

- `configure()` reads `self.ctx.section`. It runs again whenever settings change, and `build()` is then called again for fresh widgets.
- `available()` hides the module when its hardware is missing.
- `on_open()` starts work when the panel opens.
- `on_close()` stops it when the panel closes.
- `setup()` registers actions even when the card isn't shown.
- `busy()` (drawer tabs): return True while running; the drawer opens on that tab.

A card that has nothing to show should hide itself (a `Gtk.Revealer` around the card, as Notifications, Downloads, Updates and Battery do) rather than show an empty state. New features should usually be commands, not cards: put them in a tool.

`title`, `icon` and `description` show on the settings Features page, where every card can be switched off (its id goes to `[modules] hidden`, and it keeps its place) or dragged elsewhere. The layout logic is in [`modules/layout.py`](../src/cornerwindow/modules/layout.py).

To add a quick toggle, write a `Toggle` subclass with `@toggle("id")` in [`src/cornerwindow/modules/toggles.py`](../src/cornerwindow/modules/toggles.py). For a picker (like Wi-Fi networks), set `has_picker = True` and implement `picker_items()` (a list of `PickItem`) and `pick(item)`; both run on a worker thread. A tri-state toggle sets `cycles = True` and implements `cycle()`.

### Colours and themes

Custom-drawn widgets must not hard-code white. Read the current palette at draw time:

```python
from cornerwindow.ui import theme

p = theme.current()
cr.set_source_rgba(*p.track)  # also p.text, p.text_dim, p.fill, p.ink_on_fill, p.tile_off, p.chart …
```

The three palettes (`BLACK`, `GLASS_DARK`, `GLASS_LIGHT`) live in [`ui/theme.py`](../src/cornerwindow/ui/theme.py). A theme switch is only a redraw, so whatever you draw follows it. CSS follows through the `cw-glass` and `cw-light` classes on the root widget. In glass mode, give bordered things the same outer size as in black: use a 1px border with 1px less padding, so switching never moves anything (`scripts/screenshots.sh panel glass` warns when the two pictures differ in size). Avoid inset `box-shadow`, because GTK3 draws it slowly.

When editing `style.css` by search-and-replace, make sure the text you match occurs once: many rules have a `.cw-light` twin that starts the same way.

### Liquid Glass internals

- [`native/glass.c`](../src/cornerwindow/native/glass.c) is the per-pixel kernel: libc and libm only, built by [`native/__init__.py`](../src/cornerwindow/native/__init__.py) into `~/.cache/cornerwindow/glass-<hash>.so` and loaded with ctypes. Its entry points:
  - `cw_edge_field`: distance to the visible edges and smooth normals.
  - `cw_prepare_backdrop`: quarter-resolution blur.
  - `cw_shade`: lens, dispersion, tint, specular, press glow.

  Bump `ABI` in both files when you change `cw_params`.
- [`platform/backdrop/`](../src/cornerwindow/platform/backdrop) supplies what is behind a window:
  - `x11.py` composites the windows below ours over the wallpaper, live, using a private Xlib connection and ctypes.
  - `wallpaper.py` is the static fallback.
- [`ui/glass.py`](../src/cornerwindow/ui/glass.py) turns any cairo outline into kernel inputs and caches every stage, so a settled panel costs nothing until something behind it changes. While a spring runs, the island renders the glass at half resolution and keeps the backdrop still. It catches up once the panel settles.
- Set `CORNERWINDOW_GLASS_DEBUG=1` to print the time each stage of a glass frame takes.

### Platform backends

To support new hardware or a new desktop, add a backend under `platform/` behind the existing interface. For example, a new `DisplayBackend` in [`src/cornerwindow/platform/display/`](../src/cornerwindow/platform/display), or KDE support for dark mode in [`src/cornerwindow/platform/desktop/__init__.py`](../src/cornerwindow/platform/desktop/__init__.py).

## Demo mode and screenshots

`CORNERWINDOW_DEMO=1 cornerwindow` runs the real app with made-up data behind every service ([`demo.py`](../src/cornerwindow/demo.py)): two DDC monitors, three outputs, Spotify playing, notifications, a download, an event in 25 minutes, fixed sensor values. It uses its own D-Bus name and a throwaway home folder, so it never touches a running CornerWindow or your settings. Add fakes there when a new card needs data.

`cornerwindow snapshot FILE.png` saves the open panel (it opens it first), drawn offscreen over the wallpaper by [`ui/snapshot.py`](../src/cornerwindow/ui/snapshot.py), so no compositor or screen grab is needed.

`scripts/screenshots.sh` regenerates every picture in the README, headless: it starts Xvfb (3840×2160 at 2× scale) and a private session bus, runs the app in demo mode and plays the scenes in [`scripts/screenshots.py`](../scripts/screenshots.py). `scripts/screenshots.sh panel glass` makes only those; `--out DIR` writes elsewhere. To add a picture, add a scene (a generator that yields seconds to wait) and list it in `SCENES`. Needs `xvfb`, `dbus`, GTK 3, Wnck and PyGObject; the script picks the first Python that can import GTK.

## Layout of the code

```text
src/cornerwindow/
  app.py            Gtk.Application: wiring, CLI, single instance over D-Bus
  config.py         layered TOML (keeps each layer, so the UI knows where values come from)
  config_edit.py    in-place TOML editing that keeps comments; the settings writer
  services.py       lazy service container (ctx.services)
  doctor.py         `cornerwindow doctor`
  demo.py           demo mode: fake services and data (CORNERWINDOW_DEMO=1, screenshots)
  core/             springs (anim), event bus, workers (tasks), live activities, actions, state,
                    calc (sums, units, bases, currencies), clock (times, zones, dates), cmdline
                    (command-bar parsing), history (recents), emoji, launcher (app ranking), dates (month grids),
                    snap (window layout geometry), snippets (placeholder expansion), alerts (when to warn)
  platform/         all OS access, behind small interfaces with probing
    display/        native DDC/CI (ddc_i2c), ddcutil, laptop backlight, xrandr gamma, night light
    audio/          wpctl (PipeWire), pactl (PulseAudio)
    media/          MPRIS and MPD players, site detection for browser tabs (sites.py), MediaService
    window/         X11 (EWMH + Wnck) and wlr-layer-shell placement; tasklist.py: open windows by app
                    (the Apps card)
    network/        Wi-Fi (networks, joining), Bluetooth devices, VPN, airplane mode
    desktop/        dark mode, do-not-disturb and other desktop settings; taskbar.py: hiding the
                    desktop's taskbar (experimental), with what it changed saved for restoring
    power/          lock, sleep, restart, shut down, sleep inhibit, power profiles, UPower batteries
    sensors/        CPU, GPU, memory, disk and uptime
    notifications.py  notification history (a D-Bus monitor; the desktop keeps its own banners)
    downloads.py    downloads in progress / finished, from the downloads folder
    calendar.py     iCalendar parsing and recurrence; Evolution's files, .ics files and links
    capture.py      screenshots, colour picker (xdg-desktop-portal first), OCR and QR via tesseract/zbarimg
    processes.py    /proc: busiest processes, listening ports, stopping a process
    updates.py      apt / dnf / pacman / Flatpak / Snap update counts
    agents.py       Claude Code and Codex usage from their local logs
    backdrop/       what's behind our windows, for Liquid Glass (live X11 compositing, wallpaper)
    clipboard.py    clipboard access
  native/           glass.c, the Liquid Glass pixel kernel (built with cc on first use)
  ui/
    island.py       the window: morph, trigger, input shape, gestures, focus
    shape.py        quarter-circle-to-panel outline with continuous corners and screen-edge ears
    glass.py        Liquid Glass surfaces: outline + backdrop -> lensed, lit glass
    theme.py        palettes (black, glass dark, glass light) read by every custom-drawn widget
    content.py      card grid with staggered, offscreen-rendered entrance
    snapshot.py     offscreen pictures of our windows (`cornerwindow snapshot`, scripts/screenshots.py)
    widgets/        pill slider, toggle tile, sparkline, hold button, cards
    settings/       the settings window: schema.py (every option), editors.py, layout_editor.py
                    (the Features page and switch lists), window.py
    style.css       default stylesheet
  modules/          one file per feature card or tool (base.py documents the API; layout.py the layout model)
scripts/
  screenshots.sh    regenerate docs/screenshots/ headless (Xvfb + demo mode)
```

## Tests

Install pytest, then run it from the repository root:

```bash
python3 -m pip install pytest
python3 -m pytest
```

The tests cover:

- config layering, in-place TOML editing and the settings writer
- the card layout model (hide, move, area rules)
- springs and the outline geometry
- DDC/CI packet encoding and checksums, display limits (also per monitor)
- the wpctl, ddcutil, pw-dump, pactl, nmcli, UPower and MPD parsers
- media: site detection for browser tabs, player ranking, seek parsing
- the calculator, unit and currency conversion, times and dates, command-bar parsing and history, emoji search, app ranking, timer and stopwatch, month grids
- hiding and restoring the taskbar, matching windows to apps, the Apps card's order and cycling
- Open-Meteo forecast and geocoder parsing
- module loading
- notification parsing and history, the download tracker, iCalendar recurrence and Evolution's cache, next-event text
- window layout geometry, snippet expansion, alert timing, /proc and port parsers, update counters
- Claude Code / Codex log parsing (streamed duplicates, 5-hour windows, incremental reads), colour formats
