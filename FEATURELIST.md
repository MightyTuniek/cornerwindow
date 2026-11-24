# Feature list

The cards inside the open panel. The id is what goes in `[modules]` in the config (default layout in `config/default.toml`). Switch cards on and off, and move them, under Settings → Features; a switched-off card is listed in `[modules] hidden` and keeps its place. "Contextual" cards hide themselves while they have nothing to show. A column taller than the screen scrolls (the Features page warns, and switched-on cards go to the column with the most room). The Features page's **Essentials** and **Everything** buttons set the whole layout (`PRESETS` in `modules/layout.py`).

| Card | Id | Contents |
|---|---|---|
| Command bar | `commandbar` | One bar for everything (top row): open apps (most used first) and switch windows, open files, folders (Tab completes) and websites, find files (`f name`), run shell commands (`> cmd`, `>> cmd` in a terminal), every card's and tool's commands, calculate (percentages, hex/binary), convert units and currencies (`100 usd to eur`), times and dates (`time in tokyo`, `days until dec 25`), alarms, emoji (`:heart`), clipboard, notes, notification and download search, power commands (restart/shut down need Enter twice), search shortcuts (`yt lofi`), recents on ↑, web search; the **?** button lists all of it |
| Apps | `windows` | Favourite and running apps, grouped by app: click to switch (or minimise the app you're in), window list for apps with several windows, middle-click for a new window, orange dot on the trigger when a window asks for attention; shown while `[experimental] hide_taskbar` is on, or always with `[windows] show = "always"`; X11 only (top row) |
| Display | `display` | Brightness and contrast over DDC/CI, presets; per-monitor brightness sliders when unlinked; a Night light chip only when the Night quick toggle isn't shown (`night_chip`) |
| Sound | `audio` | Volume slider (past 100% with `max_volume`), mic mute, one output picker; under "Microphone & apps": mic level, input picker, per-app volume and per-app output; `out name` switches the output |
| Now Playing | `media` | Any MPRIS player or MPD: artwork, title, source ("YouTube · Firefox"), controls, seekable progress with times, shuffle/repeat, player chips; click the artwork to raise the player; green dot on the trigger while playing |
| Drives | `drives` | Removable drives and phones: mount, open, eject; shown only while one is plugged in |
| System | `glance` | CPU, GPU, memory and network in one compact row; click for sparklines, disk space, uptime and the busiest processes with a stop button |
| Clock | `header` | Clock, date, the next calendar event (Evolution / GNOME Calendar / online accounts, `.ics` files or links); click for a month calendar with dots on busy days and an agenda; pin, settings and power buttons (the power button replaces the Power card) |
| Quick toggles | `toggles` | See the table below; Wi-Fi, Bluetooth, Awake, power mode and VPN have pickers; past `rows` rows the rest sit behind a **More** tile |
| Notifications | `notifications` | History of the notifications the desktop showed (watched on the session bus); click to open the app, dismiss, `nn` searches; blue dot while unread; contextual |
| Downloads | `downloads` | Downloads in progress (size so far) and just finished, with Open and Show in folder; blue dot while downloading; contextual |
| Updates | `updates` | System (apt, dnf, pacman), Flatpak and Snap updates, a button to the updater; checked on open at most every `check_hours`; contextual |
| Battery | `battery` | Laptop battery (percent, time left, charging) from UPower, and wireless devices once they're low (`peripherals = "low"`); red dot when low; contextual |
| Drawer | `drawer` | Tabs for the drawer cards below; opens on a running timer or stopwatch |
| Timer | `timer` | Countdown timer (inside the drawer) |
| Stopwatch | `stopwatch` | Stopwatch with laps (inside the drawer); green dot on the trigger while running |
| Clipboard | `clipboard` | Clipboard history (inside the drawer): text, the last few images, pinned entries; paste as plain text (`plain`); optional clearing of the system clipboard after a delay |
| Notes | `notes` | Self-saving scratchpad (inside the drawer) |
| Shelf | `shelf` | Park files, text and links (inside the drawer): drop them on the corner or the tab, drag them out later |
| Power | `power` | Lock, sleep, log out, restart, shut down, each with press-and-hold to confirm; off by default (the clock's power button has the same buttons) |
| Weather | `weather` | Now, next hours and next days from Open-Meteo; off by default, pick a city in Settings → Weather |
| AI agents | `agents` | About twenty coding agents (Claude Code, Codex, OpenCode, Kilo, Gemini CLI, Qwen, Antigravity, Copilot CLI, Amp, Droid, pi, Oh My Pi, OpenClaw, Grok Build, Kimi, Goose, Hermes, Cline, Codebuff, ZCode, DeepSeek Harness) from their local logs, plus Cursor CLI, Aider, Kiro, Auggie, Crush, Continue, OpenHands, Mistral Vibe, Junie and Plandex by their running process: tokens and recorded cost today, Claude's 5-hour window, Codex's plan limits, models, which project an agent works or waits in (hooks via `cornerwindow agent ...`, herdr, recent logs or a busy process); `hide` leaves agents out; purple dot while one works; off by default |

## Tools

Modules without a card: they add command-bar entries and actions. The ids go in `[modules] tools`.

| Tool | Id | Commands |
|---|---|---|
| Capture | `capture` | `shot` / `shot window` / `shot screen` (Pictures/Screenshots, copied), `pick` (colour from the screen), `ocr` (text from an area, tesseract), `qr` (zbarimg); portal first, CLI tools as fallback; actions `capture.*` |
| Window layouts | `windowtools` | `snap left/right/top/bottom`, quarters (`tl`…), thirds, `max`, `center`, `next` (monitor), `restore`; actions `window.snap.*`; X11 |
| Snippets | `snippets` | `;name` pastes (xdotool / wtype) or copies; `{date}` `{time}` `{datetime}` `{weekday}` `{clipboard}`; `sn add name text` |
| Processes | `processes` | `top`, `kill name` (`kill -9`), `port 3000`, `ports`, `ip`; stopping needs Enter twice |
| Alerts | `alerts` | Notifications when CPU, temperature, memory or disk cross their limits; checks in the background; off by default |

## Quick toggles

The ids go in `[toggles] items`. Each hides itself when its service is missing.

| Toggle | Id | What it does |
|---|---|---|
| Wi-Fi | `wifi` | Radio on/off; picker joins known and open networks |
| Bluetooth | `bluetooth` | Adapter on/off; picker connects paired devices, with their battery |
| Night | `night_light` | Warmer colours |
| Dark | `dark_mode` | The desktop's dark style |
| Focus | `dnd` | Do not disturb |
| Awake | `caffeine` | Keep the screen from sleeping; picker for 30 min / 1 h / 2 h (`awake_minutes`), `awake 1h` |
| Power | `power_profile` | Balanced / performance / power saver (power-profiles-daemon) |
| VPN | `vpn` | NetworkManager VPN and WireGuard connections; picker chooses which |
| Airplane | `airplane` | Wi-Fi, Bluetooth and mobile broadband off together |
| Mute mic | `mic` | Microphone mute (not in the default list) |
| Glass | `glass` | The Liquid Glass theme (not in the default list; also under Settings → Look) |

## Experimental

| Feature | Switch | What it does |
|---|---|---|
| Hide the taskbar | Settings → Experimental, `[experimental] hide_taskbar = true`, action `taskbar.toggle` | Hides Zorin Taskbar / Dash to Panel (auto-hide with every reveal trigger off) or Dash to Dock / Ubuntu Dock (extension off) while CornerWindow runs; restored on quit or with `cornerwindow taskbar show` |

## Themes

Not a card, but it changes all of them.

| Theme | Switch | What it is |
|---|---|---|
| Black | default (`panel.glass = false`) | Solid black island |
| Liquid Glass | Settings → Look, `panel.glass = true`, the `glass` quick toggle | A lens over the live backdrop. Light or dark, tuned under `[glass]` |
| Compact | Settings → Look → Density, `panel.density = "compact"` | Tighter cards and smaller big numbers, in either theme |
