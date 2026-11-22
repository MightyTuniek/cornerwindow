# Feature list

The cards inside the open panel. The id is what goes in `[modules]` in the config (default layout in `config/default.toml`).

| Card | Id | Contents |
|---|---|---|
| Command bar | `commandbar` | Type to run commands and calculations (top row) |
| Display | `display` | Brightness and contrast over DDC/CI, presets, night light |
| Sound | `audio` | Volume slider, mic mute, output device switcher |
| Now Playing | `media` | Artwork, title, controls and progress for MPRIS players; shown only while something plays |
| System | `glance` | CPU, GPU, memory and network sparklines, disk space, uptime |
| Clock | `header` | Clock, date, pin and settings buttons |
| Quick toggles | `toggles` | Wi-Fi, Bluetooth, night light, dark mode, focus (DND), awake, mic mute, Liquid Glass theme |
| Drawer | `drawer` | Tabs for the drawer cards below |
| Timer | `timer` | Countdown timer (inside the drawer) |
| Clipboard | `clipboard` | Clipboard history (inside the drawer) |
| Notes | `notes` | Self-saving scratchpad (inside the drawer) |
| Power | `power` | Lock, sleep, restart, shut down, each with press-and-hold to confirm |
| Weather | `weather` | Current weather from Open-Meteo; off by default, needs latitude/longitude |

## Themes

Not a card, but it changes all of them.

| Theme | Switch | What it is |
|---|---|---|
| Black | default (`panel.glass = false`) | Solid black island |
| Liquid Glass | `glass` quick toggle, Settings → Look, `panel.glass = true` | A lens over the live backdrop. Light or dark, tuned under `[glass]` |
