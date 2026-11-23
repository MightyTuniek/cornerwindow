"""Demo mode: the real app with made-up, fixed data, for screenshots and trying it out.

    CORNERWINDOW_DEMO=1 cornerwindow        # every card has something to show, nothing touches hardware

Every service a card talks to is replaced by a fake with believable values (two monitors over
DDC, three outputs, Spotify playing, a few notifications, a download, an event in 25 minutes…);
sensors, process lists and update checks return fixed numbers. Cards themselves run unchanged.
`scripts/screenshots.py` uses this to regenerate the README's screenshots.

Nothing here runs unless CORNERWINDOW_DEMO is set.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path

ENV = "CORNERWINDOW_DEMO"


def enabled() -> bool:
    return os.environ.get(ENV, "") not in ("", "0", "false")


def assets_dir() -> Path:
    from .core import paths

    folder = paths.data_dir() / "demo"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# -- pictures, drawn here so the repository needs no binary assets --------------------------
def wallpaper(path: Path, width: int = 3840, height: int = 2160) -> Path:
    """Deep blue into violet into pink, like the default wallpapers of the last few years."""
    import cairo

    if path.exists():
        return path
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, width, height)
    cr = cairo.Context(surface)
    base = cairo.LinearGradient(0, 0, width * 0.35, height)
    base.add_color_stop_rgb(0.0, 0.17, 0.36, 0.78)
    base.add_color_stop_rgb(0.55, 0.30, 0.22, 0.62)
    base.add_color_stop_rgb(1.0, 0.62, 0.22, 0.42)
    cr.set_source(base)
    cr.paint()
    for cx, cy, r, color in ((0.15, 0.2, 0.55, (0.25, 0.55, 0.95, 0.35)), (0.85, 0.95, 0.5, (0.95, 0.35, 0.55, 0.4)),
                             (0.6, 0.45, 0.35, (0.45, 0.3, 0.85, 0.25))):
        glow = cairo.RadialGradient(cx * width, cy * height, 0, cx * width, cy * height, r * width)
        glow.add_color_stop_rgba(0, *color)
        glow.add_color_stop_rgba(1, *color[:3], 0)
        cr.set_source(glow)
        cr.paint()
    surface.write_to_png(str(path))
    return path


def album_art(path: Path, size: int = 300) -> Path:
    """A sunset over hills, for the song that plays in the demo."""
    import cairo

    if path.exists():
        return path
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, size, size)
    cr = cairo.Context(surface)
    sky = cairo.LinearGradient(0, 0, 0, size)
    sky.add_color_stop_rgb(0, 0.95, 0.35, 0.45)
    sky.add_color_stop_rgb(0.6, 0.98, 0.55, 0.4)
    sky.add_color_stop_rgb(1, 0.55, 0.2, 0.45)
    cr.set_source(sky)
    cr.paint()
    cr.arc(size * 0.5, size * 0.55, size * 0.17, 0, 2 * math.pi)
    cr.set_source_rgb(1.0, 0.86, 0.55)
    cr.fill()
    for i, (y, color) in enumerate(((0.62, (0.55, 0.2, 0.45)), (0.72, (0.35, 0.13, 0.35)), (0.83, (0.2, 0.08, 0.25)))):
        cr.move_to(0, size)
        cr.line_to(0, size * y)
        for x in range(0, size + 10, 10):
            cr.line_to(x, size * y + math.sin(x / size * 6 + i * 2) * size * 0.035)
        cr.line_to(size, size)
        cr.close_path()
        cr.set_source_rgb(*color)
        cr.fill()
    surface.write_to_png(str(path))
    return path


def calendar_file(path: Path) -> Path:
    """Events around now, so "next event" always reads the same (always rewritten)."""
    now = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
    fmt = "%Y%m%dT%H%M%SZ"

    def event(summary: str, start: dt.datetime, minutes: int) -> str:
        return (f"BEGIN:VEVENT\nUID:{summary}\nSUMMARY:{summary}\nDTSTART:{start.strftime(fmt)}\n"
                f"DTEND:{(start + dt.timedelta(minutes=minutes)).strftime(fmt)}\nEND:VEVENT\n")

    tomorrow = (now + dt.timedelta(days=1)).replace(hour=8, minute=0)
    body = (event("Design review", now + dt.timedelta(minutes=25), 30)
            + event("Lunch with Anna", now + dt.timedelta(hours=2, minutes=5), 60)
            + event("Dentist", tomorrow, 45)
            + event("Release planning", now + dt.timedelta(days=3, hours=1), 60))
    path.write_text(f"BEGIN:VCALENDAR\n{body}END:VCALENDAR\n")
    return path


def agent_logs(home: Path) -> None:
    """A morning of Claude Code work, so the AI agents card has something to count."""
    folder = home / ".claude" / "projects" / "-home-demo-cornerwindow"
    folder.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    lines = []
    for i in range(60):
        at = now - dt.timedelta(minutes=95 - i * 1.5)
        lines.append(json.dumps({"type": "assistant", "timestamp": at.isoformat(), "requestId": f"r{i}",
                                 "message": {"id": f"m{i}", "model": "claude-opus-5-5",
                                             "usage": {"input_tokens": 40, "output_tokens": 900 + i * 7,
                                                       "cache_read_input_tokens": 60000,
                                                       "cache_creation_input_tokens": 2500}}}))
    (folder / "demo-session.jsonl").write_text("\n".join(lines) + "\n")


# -- fakes ---------------------------------------------------------------------------------
class FakeNightLight:
    available = True

    def __init__(self) -> None:
        self.on, self.amount, self._listeners = False, 0.6, []

    def is_on(self) -> bool:
        return self.on

    def set(self, on: bool) -> None:
        self.on = on
        for fn in self._listeners:
            fn()

    def warmth(self) -> float:
        return self.amount

    def set_warmth(self, value: float) -> None:
        self.amount = value

    def connect(self, fn) -> None:
        self._listeners.append(fn)


def display_backend():
    from .platform.display.base import BRIGHTNESS, CONTRAST, Control, DisplayBackend, Monitor

    class Backend(DisplayBackend):
        name = "demo"

        def probe(self):
            return [Monitor(f"demo:{i}", name, f"DP-{i}", self, {BRIGHTNESS: Control(BRIGHTNESS, 62),
                                                                CONTRAST: Control(CONTRAST, 70)})
                    for i, name in enumerate(("DELL U2723QE", "LG 27GP850"))]

        def write(self, monitor, control, raw) -> None:
            monitor.controls[control].value = raw

    return Backend()


def audio_backend():
    from .platform.audio.base import AudioBackend, Device, Level, Stream

    class Backend(AudioBackend):
        name = "demo"

        def __init__(self) -> None:
            self.levels = {"sink": Level(0.64, False), "source": Level(0.8, False)}
            self.sinks = [Device("50", "Sennheiser HD 660S", True), Device("51", "Studio Speakers"),
                          Device("52", "DELL U2723QE (HDMI)")]
            self.sources = [Device("60", "Shure MV7", True), Device("61", "Webcam C920")]
            self.apps = [Stream("70", "Spotify", "spotify", 0.8, False, "50"),
                         Stream("71", "Firefox", "firefox", 0.55, False, "50"),
                         Stream("72", "Discord", "discord", 0.7, False, "51")]

        @staticmethod
        def available() -> bool:
            return True

        def get(self, kind):
            return self.levels[kind]

        def set_volume(self, kind, volume) -> None:
            self.levels[kind] = Level(volume, self.levels[kind].muted)

        def set_muted(self, kind, muted) -> None:
            self.levels[kind] = Level(self.levels[kind].volume, muted)

        def devices(self, kind):
            return list(self.sinks if kind == "sink" else self.sources)

        def set_default(self, kind, device_id) -> None:
            for d in self.sinks if kind == "sink" else self.sources:
                d.default = d.id == device_id

        def streams(self):
            return list(self.apps)

        def move_stream(self, stream_id, sink_id) -> None:
            for s in self.apps:
                if s.id == stream_id:
                    s.sink = sink_id

    return Backend()


@dataclass
class FakePlayer:
    key: str
    identity: str
    app: str
    title: str
    artist: str
    art: str
    status: str
    length: float
    position: float
    site: str = ""
    last_active: float = 0.0

    def __post_init__(self) -> None:
        self._at = time.monotonic()

    def playing(self) -> bool:
        return self.status == "Playing"

    def names(self) -> str:
        return f"{self.key} {self.identity}"

    def snapshot(self):
        from .platform.media.model import NowPlaying

        return NowPlaying(self.key, self.identity, self.status, self.title, self.artist, self.art, self.length,
                          self.position, self._at, app=self.app, site=self.site, can_seek=True, can_raise=True,
                          shuffle=False, loop="None")

    def play_pause(self) -> None:
        self.position = self.snapshot().position_now()
        self._at = time.monotonic()
        self.status = "Paused" if self.playing() else "Playing"

    def next(self) -> None:
        self.position, self._at = 0.0, time.monotonic()

    previous = next

    def seek_to(self, seconds: float) -> None:
        self.position, self._at = seconds, time.monotonic()

    def set_shuffle(self, on: bool) -> None:
        pass

    def set_loop(self, loop: str) -> None:
        pass

    def fetch_position(self) -> None:
        pass

    def raise_(self) -> None:
        pass


class _Simple:
    """A switch that remembers its state."""

    def __init__(self, on: bool = False) -> None:
        self.on = on

    def available(self) -> bool:
        return True

    def is_on(self) -> bool:
        return self.on

    def set(self, on: bool, *_a, **_k) -> None:
        self.on = on

    def watch(self, _fn) -> None:
        pass


class FakeWifi(_Simple):
    def connection(self) -> str:
        return "Home" if self.on else ""

    def networks(self, rescan: bool = True):
        from .platform.network import WifiNetwork

        return [WifiNetwork("Home", 90, True, True, True), WifiNetwork("Home 5G", 80, True, False, True),
                WifiNetwork("Café Corner", 60, False, False, False)]

    def rescan(self) -> None:
        pass

    def connect(self, network) -> str:
        return f"Connected to {network.ssid}"


class FakeBluetooth(_Simple):
    def devices(self):
        from .platform.network import BtDevice

        return [BtDevice("/k", "MX Keys", "input-keyboard", True, 64),
                BtDevice("/m", "MX Master 3S", "input-mouse", True, 72),
                BtDevice("/h", "WH-1000XM5", "audio-headphones", False, 40),
                BtDevice("/g", "DualSense", "input-gaming", True, 18)]

    def connected_name(self) -> str:
        return "MX Keys"

    def toggle_device(self, device) -> str:
        return device.name


class FakeVpn:
    def __init__(self) -> None:
        self.up_name = ""

    def available(self) -> bool:
        return True

    def connections(self):
        return [(name, name == self.up_name) for name in ("Work", "Mullvad")]

    def is_on(self) -> bool:
        return bool(self.up_name)

    def active(self) -> str:
        return self.up_name

    def up(self, name: str) -> bool:
        self.up_name = name
        return True

    def down(self, _name: str) -> None:
        self.up_name = ""


class FakeAirplane(_Simple):
    def radios_on(self) -> bool:
        return not self.on


class FakeProfiles:
    def __init__(self) -> None:
        self.current = "balanced"

    def available(self) -> bool:
        return True

    def profiles(self):
        return ["power-saver", "balanced", "performance"]

    def active(self) -> str:
        return self.current

    def set(self, profile: str) -> None:
        self.current = profile

    def cycle(self) -> str:
        order = self.profiles()
        self.current = order[(order.index(self.current) + 1) % len(order)]
        return self.current


class FakeBatteries:
    running = True

    def __init__(self) -> None:
        from .platform.power.upower import BatteryDevice

        self.devices = [BatteryDevice("/g", "gamepad", "DualSense", 18, "discharging", 0, 0, False),
                        BatteryDevice("/h", "headphones", "WH-1000XM5", 40, "discharging", 0, 0, False),
                        BatteryDevice("/m", "mouse", "MX Master 3S", 72, "discharging", 0, 0, False)]

    def start(self) -> None:
        pass

    def current(self):
        return list(self.devices)


# -- installing it -------------------------------------------------------------------------
def install(app) -> None:
    """Swap every service of a freshly made app for a fake (call before the cards are built)."""
    from .platform import processes, sensors, updates
    from .platform.audio.service import AudioService
    from .platform.backdrop import wallpaper as wallpaper_mod
    from .platform.display import service as display_service
    from .platform.media.service import MediaService
    from .platform.notifications import History, from_notify

    services = app.services
    assets = assets_dir()
    paper = wallpaper(assets / "wallpaper.png")
    art = album_art(assets / "golden-hour-drive.png")
    home = Path.home()

    # display: two DDC monitors
    display_service.build_backends = lambda _config: [display_backend()]
    services.__dict__["nightlight"] = FakeNightLight()

    # sound
    audio = AudioService(app.config.section("audio"), app.bus)
    audio.backend = audio_backend()
    audio.levels = dict(audio.backend.levels)
    services.__dict__["audio"] = audio

    # now playing: Spotify, and YouTube paused in Firefox
    media = MediaService(app.bus, app.config.section("media"))
    media.add(FakePlayer("spotify", "spotify", "Spotify", "Golden Hour Drive", "Neon Coast", art.as_uri(),
                         "Playing", 215, 137, last_active=time.monotonic()))
    media.add(FakePlayer("firefox.instance1", "firefox", "Firefox", "Lo-fi beats to code to", "Chillhop",
                         "", "Paused", 3600, 1210, site="YouTube"))
    services.__dict__["media"] = media

    # quick toggles
    services.__dict__.update(wifi=FakeWifi(True), bluetooth=FakeBluetooth(True), vpn=FakeVpn(),
                             airplane=FakeAirplane(False), power_profiles=FakeProfiles(),
                             dark_mode=_Simple(True), dnd=_Simple(False), caffeine=_Simple(True),
                             battery=FakeBatteries())

    # notifications
    history = History(30)
    history.monitor = type("Monitor", (), {"running": True})()
    now = time.time()
    for app_name, icon, summary, body, entry, ago in (
            ("Calendar", "x-office-calendar-symbolic", "Design review in 25 minutes", "Room 3 · with Sam and Priya",
             "org.gnome.Calendar", 300),
            ("Slack", "user-available-symbolic", "Anna", "Lunch at 1? The new ramen place opened", "slack", 240),
            ("Firefox", "folder-download-symbolic", "Download complete", "Q4 report.pdf", "firefox", 60)):
        history.add(from_notify((app_name, 0, icon, summary, body, [], {"desktop-entry": entry}, -1), at=now - ago))
    services.__dict__["notifications"] = history

    # downloads: one coming in, one just finished
    (home / "Downloads").mkdir(parents=True, exist_ok=True)
    downloads = services.downloads  # the real watcher, on the demo home's Downloads folder
    folder = downloads.folder
    downloads.tracker.changed(folder / "ubuntu-26.04-desktop-amd64.iso.part", 2_400_000_000, now=now - 40)
    downloads.tracker.created(folder / "Q4 report.pdf", 2_516_582, now=now - 60)

    # sensors and processes
    class Cpu:
        def sample(self) -> float:
            return 8 + 4 * math.sin(time.time() / 3)

    class Net:
        def sample(self):
            return 2_700_000 + 900_000 * math.sin(time.time()), 91_700.0

    class Temp:
        def __init__(self, value: float) -> None:
            self.value = value

        def read(self) -> float:
            return self.value

    sensors.CpuUsage = Cpu
    sensors.NetSpeed = Net
    sensors.find_sensor = lambda _spec, kind: Temp(54 if kind == "cpu" else 47)
    sensors.gpu_busy = lambda: 50.0
    sensors.memory = lambda: (9.6, 32.0)
    sensors.disk = lambda _path="/": (412.0, 1000.0)
    sensors.uptime = lambda: 3 * 86400 + 4 * 3600
    processes.ProcessSampler.top = lambda self, count=5, now=None: [
        processes.Proc(4242, "firefox", 6.0, 1840), processes.Proc(4343, "code", 3.1, 920),
        processes.Proc(4444, "spotify", 1.2, 410), processes.Proc(4545, "gnome-shell", 0.9, 380)][:count]

    # updates, the glass theme's wallpaper, the calendar, agent logs
    updates.check = lambda flatpak=True, snap=True: updates.Updates(system=12, security=3, flatpak=2, source="apt")
    wallpaper_mod.Wallpaper._settings = lambda self: (paper, "zoom", (0.1, 0.1, 0.12))
    calendar_file(assets / "calendar.ics")
    agent_logs(home)

    # the clipboard has a little history
    clipboard = services.clipboard
    for text in ("ssh deploy@build-01.internal", "https://github.com/cornerwindow/cornerwindow",
                 "Thanks, see you at 1!", "rgb(48, 209, 88)"):
        clipboard.items.insert(0, text)


def config_text(assets: Path) -> str:
    """The demo user's config.toml: the default layout, with the demo calendar."""
    return (f'[calendar]\nevolution = false\nsources = ["{assets / "calendar.ics"}"]\n\n'
            '[updates]\ncheck_hours = 0\n')


def prepare_home(home: Path) -> None:
    """A throwaway home folder for demo mode (screenshots run with HOME pointing here)."""
    config = home / ".config" / "cornerwindow"
    config.mkdir(parents=True, exist_ok=True)
    data = home / ".local" / "share" / "cornerwindow" / "demo"
    data.mkdir(parents=True, exist_ok=True)
    (config / "config.toml").write_text(config_text(data))
    (home / "Downloads").mkdir(exist_ok=True)



def isolate() -> None:
    """Demo mode never touches your real config: point HOME at a throwaway folder (unless the
    caller, e.g. the screenshot script, already did) and fill it."""
    if not os.environ.get("CORNERWINDOW_DEMO_HOME"):
        home = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "cornerwindow" / "demo-home"
        os.environ["CORNERWINDOW_DEMO_HOME"] = str(home)
    home = Path(os.environ["CORNERWINDOW_DEMO_HOME"])
    os.environ["HOME"] = str(home)
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        os.environ.pop(var, None)
    prepare_home(home)
