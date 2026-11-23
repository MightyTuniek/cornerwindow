# Feature list

The cards inside the open panel. The id is what goes in `[modules]` in the config (default layout in `config/default.toml`). Switch cards on and off, and move them, under Settings → Features; a switched-off card is listed in `[modules] hidden` and keeps its place.

| Card | Id | Contents |
|---|---|---|
| Command bar | `commandbar` | Type to open apps, switch to open windows, run commands, calculate, convert units (`5 km to mi`) and search the web (top row) |
| Apps | `windows` | Favourite and running apps, grouped by app: click to switch (or minimise the app you're in), window list for apps with several windows, middle-click for a new window, orange dot on the trigger when a window asks for attention; shown while `[experimental] hide_taskbar` is on, or always with `[windows] show = "always"`; X11 only (top row) |
| Display | `display` | Brightness and contrast over DDC/CI, presets, night light; per-monitor brightness sliders when unlinked |
| Sound | `audio` | Volume slider, mic mute, output device switcher; under "Microphone & apps": mic level, input device switcher, per-app volume |
| Now Playing | `media` | Any MPRIS player or MPD: artwork, title, source ("YouTube · Firefox"), controls, seekable progress with times, shuffle/repeat, player chips; click the artwork to raise the player; green dot on the trigger while playing |
| Drives | `drives` | Removable drives and phones: mount, open, eject; shown only while one is plugged in |
| System | `glance` | CPU, GPU, memory and network sparklines, disk space, uptime |
| Clock | `header` | Clock, date (click for a month calendar), pin and settings buttons |
| Quick toggles | `toggles` | See the table below; Wi-Fi, Bluetooth, power mode and VPN have pickers |
| Battery | `battery` | Laptop battery (percent, time left, charging) and wireless devices from UPower; red dot when low; shown only when there is a battery |
| Drawer | `drawer` | Tabs for the drawer cards below |
| Timer | `timer` | Countdown timer (inside the drawer) |
| Stopwatch | `stopwatch` | Stopwatch with laps (inside the drawer); green dot on the trigger while running |
| Clipboard | `clipboard` | Clipboard history (inside the drawer) |
| Notes | `notes` | Self-saving scratchpad (inside the drawer) |
| Power | `power` | Lock, sleep, log out, restart, shut down, each with press-and-hold to confirm |
| Weather | `weather` | Now, next hours and next days from Open-Meteo; off by default, pick a city in Settings → Weather |

## Quick toggles

The ids go in `[toggles] items`. Each hides itself when its service is missing.

| Toggle | Id | What it does |
|---|---|---|
| Wi-Fi | `wifi` | Radio on/off; picker joins known and open networks |
| Bluetooth | `bluetooth` | Adapter on/off; picker connects paired devices, with their battery |
| Night | `night_light` | Warmer colours |
| Dark | `dark_mode` | The desktop's dark style |
| Focus | `dnd` | Do not disturb |
| Awake | `caffeine` | Keep the screen from sleeping |
| Power | `power_profile` | Balanced / performance / power saver (power-profiles-daemon) |
| VPN | `vpn` | NetworkManager VPN and WireGuard connections; picker chooses which |
| Airplane | `airplane` | Wi-Fi, Bluetooth and mobile broadband off together |
| Mute mic | `mic` | Microphone mute (not in the default list) |
| Glass | `glass` | The Liquid Glass theme |

## Experimental

| Feature | Switch | What it does |
|---|---|---|
| Hide the taskbar | Settings → Experimental, `[experimental] hide_taskbar = true`, action `taskbar.toggle` | Hides Zorin Taskbar / Dash to Panel (auto-hide with every reveal trigger off) or Dash to Dock / Ubuntu Dock (extension off) while CornerWindow runs; restored on quit or with `cornerwindow taskbar show` |

## Themes

Not a card, but it changes all of them.

| Theme | Switch | What it is |
|---|---|---|
| Black | default (`panel.glass = false`) | Solid black island |
| Liquid Glass | `glass` quick toggle, Settings → Look, `panel.glass = true` | A lens over the live backdrop. Light or dark, tuned under `[glass]` |
