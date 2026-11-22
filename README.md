# CornerWindow

A tiny black quarter-circle hides in a corner of your screen. Click it and it springs open into a quick panel, with cards pouring out of the corner one after another. Click outside, press <kbd>Esc</kbd> or click the corner again and it folds back up.

It runs on any Linux desktop: GNOME, KDE, Hyprland, Sway and others, on X11 or Wayland. Each feature checks whether it works on your machine and hides itself if it doesn't.

## What it does

**While collapsed** (it stays almost invisible):
- Hover over it and it grows a little and shows a faint rim of light.
- Scroll over it to change **monitor brightness** without opening anything. Shift+scroll changes volume.
- Middle-click mutes. Right-click plays or pauses media.
- **Live activities.** The circle grows to show an arc and a value when brightness or volume changes. It also shows a ring while a timer runs, a red dot while your mic is muted, and a yellow dot while Awake mode is on.
- It hides itself while a fullscreen game or video has focus.

**Opened:**

| Card | What's in it |
|---|---|
| Display | **Brightness and contrast** over DDC/CI (real monitor settings, not a dimming filter). Presets: Day, Evening, Night, Movie. Click one to apply it; hold one to save your current values into it. Also night light with a warmth slider. |
| Sound | Volume slider, mic mute, and buttons to switch the output device |
| Now playing | Artwork, title, controls and progress for any MPRIS player (browsers, Spotify, VLC…). The card only appears while something is playing. |
| Toggles | Wi-Fi, Bluetooth, Night light, Dark mode, Focus (do not disturb), Awake (prevents sleep), Mute mic |
| System | CPU % and temperature, GPU % and temperature, memory, network ↓/↑, each with a sparkline. Also free disk space and uptime. |
| Drawer | Timer, clipboard history and a scratchpad that saves itself |
| Power | Lock, Sleep, Restart, Shut down. Each needs a press and hold to confirm. |
| Command bar | **Start typing anywhere** in the open panel. Examples: `b 40`, `c 60`, `v 20`, `night`, `evening`, `timer 10`, `t 90s`, `mute`, `lock`, `2*(3+4)`. Pressing Enter on a sum copies the answer. |

### Why it feels smooth
- Every movement uses spring physics (SwiftUI-style `response`/`damping`), driven by the frame clock at your monitor's refresh rate.
- Springs keep their velocity, so clicking again in the middle of an animation reverses it smoothly instead of snapping.
- The window is never resized or moved while animating. It is sized once, everything outside the outline is transparent and click-through, and only the outline is redrawn.
- During the morph, each card is drawn once into an offscreen image and then just blended in. Nothing is laid out again mid-animation.
- Sliders follow the pointer exactly and stretch with a rubber-band effect at the ends. Hardware catches up in the background through a writer that only sends the latest value, so dragging never lags.
- Nothing ticks while the panel is collapsed, so it uses 0% CPU when idle.

Set `CORNERWINDOW_FPS=1` to print frame statistics for each animation.

## Install

```bash
./install.sh
```

The script:
- Installs any missing distro packages (apt, dnf, pacman or zypper).
- **Asks for sudo once** to add a udev rule, so you can control your monitor over I²C without root.
- Puts `cornerwindow` in `~/.local/bin`.
- Starts CornerWindow with your session.
- On GNOME, binds <kbd>Super</kbd>+<kbd>\\</kbd> to open and close it.

At the end it runs `cornerwindow doctor`.

Other options: `./install.sh --no-sudo`, `--no-hotkey` and `--uninstall`.

> **External monitors:** turn on **DDC/CI** in the monitor's own on-screen menu. Most monitors ship with it on. If `doctor` says the monitor doesn't answer, that setting is the usual cause.

## Use from the terminal

```bash
cornerwindow doctor
```

```bash
cornerwindow toggle
```

```bash
cornerwindow set brightness 40
```

```bash
cornerwindow set brightness +10
```

```bash
cornerwindow get contrast
```

```bash
cornerwindow actions
```

```bash
cornerwindow action display.preset.night
```

What they do:
- `doctor` checks what works on this machine and how to fix what doesn't.
- `toggle`, `open` and `close` control the panel.
- `set` and `get` work with `brightness`, `contrast` and `volume`.
- `actions` lists the named actions you can bind to gestures; `action NAME` runs one.

For the fastest possible hotkey, bind the D-Bus call directly. This is what install.sh does:

```bash
gdbus call --session --dest io.github.cornerwindow --object-path /io/github/cornerwindow --method org.gtk.Actions.Activate toggle [] {}
```

## Settings

Click the ⚙ in the panel, or run `cornerwindow settings` (optionally with a page, e.g. `cornerwindow settings display`). A smaller black window docks beside the panel. Every option is in it, grouped into General, Trigger, Look, Motion, Layout, Display, Sound, Toggles, System, Clock, Timer, Drawer, Clipboard, Power and Weather. A **Files** page shows each config file and has a raw editor for yours.

- **Changes apply live.** The panel rebuilds in place and springs to its new size. A running timer, your clipboard history and the settings window itself are kept.
- **Changes are written to your file** (`~/.config/cornerwindow/config.toml`), and only the values that differ from the defaults and machine file go there. Your comments and layout in that file are kept, because the file is edited in place rather than rewritten.
- **Edits to any config file show up in the window right away.** That includes the defaults and the machine file, edited in any editor; the files are watched.
- **Badges show where each value comes from.** *this PC* means your machine file; *changed* means your own file, and those rows get a ↺ button to go back to the default.
- **Options the window doesn't know about still appear.** New keys, and sections belonging to your own modules, get an editor picked from the value's type.

## Configure

Configuration is built from three layers. Later layers win; tables merge and lists replace.

1. [`config/default.toml`](config/default.toml): every option, with comments
2. [`config/machines/*.toml`](config/machines): per-PC tuning, picked by a `[match]` table on hostname or monitor model
3. `~/.config/cornerwindow/config.toml`: your own overrides (what the settings window writes)

For example, `~/.config/cornerwindow/config.toml`:

```toml
[general]
corner = "top-left"

[trigger]
radius = 16
scroll = "audio.volume.step"   # scroll changes volume instead of brightness

[modules]
columns = [["header", "display", "audio"], ["toggles", "drawer", "power"]]
```

To restyle anything, put CSS in `~/.config/cornerwindow/style.css`.

[`config/machines/my-desktop.toml`](config/machines/my-desktop.toml) is tuned for this PC:
- Zorin OS 18 on X11
- Acer VG240Y on HDMI, driven directly over `/dev/i2c-4`
- AMD RX 580 and Ryzen temperature sensors
- Friendly names for the Speakers and Monitor outputs
- Bluetooth hidden, because this PC has no adapter

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

Everything a module needs is on `self.ctx`:

| | |
|---|---|
| `section` | this module's `[uptime_card]` config table |
| `services` | `display`, `audio`, `media`, `nightlight`, `wifi`, `bluetooth`, `dark_mode`, `dnd`, `caffeine`, `clipboard` |
| `actions.register(name, fn)` | named actions that config can bind to trigger gestures |
| `activity.show(id, level=…, label=…, dot=…, color=…)` | a live activity on the collapsed trigger |
| `bus.subscribe(topic, fn)` | events such as `display.changed`, `audio.changed`, `media.changed` |
| `every(sec, fn)`, `run_async(fn, then)`, `notify(title, body)`, `state`, `data_dir`, `island` | helpers |

Override `configure()` to read `self.config`. It runs again whenever settings change, and `build()` is then called again for fresh widgets. Override `available()` to hide the module when its hardware is missing, `on_open()` and `on_close()` to start and stop work, and `setup()` to register actions even when the card isn't shown. To add a quick toggle, write a `Toggle` subclass with `@toggle("id")` in [`modules/toggles.py`](src/cornerwindow/modules/toggles.py).

To support new hardware or a new desktop, add a backend under `platform/` behind the existing interface. For example, a new `DisplayBackend` in [`platform/display/`](src/cornerwindow/platform/display), or KDE support for dark mode in [`platform/desktop/`](src/cornerwindow/platform/desktop/__init__.py).

## Layout of the code

```
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
    network/ desktop/ power/ sensors/ clipboard.py
  ui/
    island.py       the window: morph, trigger, input shape, gestures, focus
    shape.py        quarter-circle-to-panel outline with continuous corners and concave ears
    content.py      card grid with staggered, offscreen-rendered entrance
    widgets/        pill slider, toggle tile, sparkline, hold button, cards
    settings/       the settings window: schema.py (every option), editors.py, window.py
    style.css
  modules/          one file per feature card (base.py documents the API)
```

## Tests

```bash
python3 -m pytest
```

The tests cover config layering, in-place TOML editing and the settings writer, springs, the outline geometry, DDC/CI packet encoding and checksums, the wpctl and ddcutil parsers, the calculator, module loading and timer parsing.
