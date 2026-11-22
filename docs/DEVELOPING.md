# Developing CornerWindow

## Writing a module

A module is one file. Drop it into `~/.config/cornerwindow/modules/` (or `src/cornerwindow/modules/` for a built-in), then add its id to a column in `[modules]`.

```python
# ~/.config/cornerwindow/modules/uptime_card.py
from cornerwindow.modules.base import Command, Module, module
from cornerwindow.platform import sensors
from cornerwindow.ui.widgets import Card, label


@module("uptime_card", title="Uptime")
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

### Context

Everything a module needs is on `self.ctx`:

| Member | Purpose |
|---|---|
| `section` | This module's `[uptime_card]` config table |
| `services` | `display`, `audio`, `media`, `nightlight`, `wifi`, `bluetooth`, `dark_mode`, `dnd`, `caffeine`, `clipboard` |
| `actions.register(name, fn)` | Register a named action that config can bind to trigger gestures |
| `activity.show(id, level=..., label=..., dot=..., color=...)` | Show a live activity on the collapsed trigger |
| `bus.subscribe(topic, fn)` | Subscribe to events such as `display.changed`, `audio.changed`, `media.changed` |
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

To add a quick toggle, write a `Toggle` subclass with `@toggle("id")` in [`src/cornerwindow/modules/toggles.py`](../src/cornerwindow/modules/toggles.py).

### Colours and themes

Custom-drawn widgets must not hard-code white. Read the current palette at draw time:

```python
from cornerwindow.ui import theme

p = theme.current()
cr.set_source_rgba(*p.track)  # also p.text, p.text_dim, p.fill, p.ink_on_fill, p.tile_off, p.chart …
```

The three palettes (`BLACK`, `GLASS_DARK`, `GLASS_LIGHT`) live in [`ui/theme.py`](../src/cornerwindow/ui/theme.py). A theme switch is only a redraw, so whatever you draw follows it. CSS follows through the `cw-glass` and `cw-light` classes on the root widget. In glass mode, give bordered things the same outer size as in black: use a 1px border with 1px less padding, so switching never moves anything. Avoid inset `box-shadow`, because GTK3 draws it slowly.

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

## Layout of the code

```text
src/cornerwindow/
  app.py            Gtk.Application: wiring, CLI, single instance over D-Bus
  config.py         layered TOML (keeps each layer, so the UI knows where values come from)
  config_edit.py    in-place TOML editing that keeps comments; the settings writer
  services.py       lazy service container (ctx.services)
  doctor.py         `cornerwindow doctor`
  core/             springs (anim), event bus, workers (tasks), live activities, actions, state, calc
  platform/         all OS access, behind small interfaces with probing
    display/        native DDC/CI (ddc_i2c), ddcutil, laptop backlight, xrandr gamma, night light
    audio/          wpctl (PipeWire), pactl (PulseAudio)
    media/          MPRIS
    window/         X11 (EWMH + Wnck) and wlr-layer-shell placement
    network/        Wi-Fi and network throughput
    desktop/        dark mode, do-not-disturb and other desktop settings
    power/          lock, sleep, restart, shut down, sleep inhibit
    sensors/        CPU, GPU, memory, disk and uptime
    backdrop/       what's behind our windows, for Liquid Glass (live X11 compositing, wallpaper)
    clipboard.py    clipboard access
  native/           glass.c, the Liquid Glass pixel kernel (built with cc on first use)
  ui/
    island.py       the window: morph, trigger, input shape, gestures, focus
    shape.py        quarter-circle-to-panel outline with continuous corners and screen-edge ears
    glass.py        Liquid Glass surfaces: outline + backdrop -> lensed, lit glass
    theme.py        palettes (black, glass dark, glass light) read by every custom-drawn widget
    content.py      card grid with staggered, offscreen-rendered entrance
    widgets/        pill slider, toggle tile, sparkline, hold button, cards
    settings/       the settings window: schema.py (every option), editors.py, window.py
    style.css       default stylesheet
  modules/          one file per feature card (base.py documents the API)
```

## Tests

Install pytest, then run it from the repository root:

```bash
python3 -m pip install pytest
python3 -m pytest
```

The tests cover:

- config layering, in-place TOML editing and the settings writer
- springs and the outline geometry
- DDC/CI packet encoding and checksums
- the wpctl and ddcutil parsers
- the calculator and timer parsing
- module loading
