"""`cornerwindow doctor`: explain what works on this machine and how to fix what doesn't."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from . import config
from .core import paths

OK, WARN, BAD = "\033[32m✔\033[0m", "\033[33m•\033[0m", "\033[31m✘\033[0m"


def _line(mark: str, title: str, detail: str = "") -> None:
    print(f"  {mark} {title}" + (f"  \033[2m{detail}\033[0m" if detail else ""))


def run() -> int:
    from .platform import session
    from .platform.display import drm

    print("\nCornerWindow doctor\n")
    info = session.detect()
    print("Session")
    _line(OK, f"{info.type} on {info.desktop or 'unknown desktop'}")
    if session.gdk_backend_override(info):
        _line(WARN, "GNOME Wayland: will run under XWayland (no layer-shell on GNOME)")

    facts = config.gather_facts(drm.monitor_models())
    cfg = config.load(facts)
    print("\nConfig")
    for source in cfg.sources:
        _line(OK, str(source))
    if len(cfg.sources) == 1:
        _line(WARN, "no machine file matched", f"hostname={facts['hostname']}, monitors={facts['monitor_models']}")

    print("\nMonitors")
    problems = 0
    for conn in drm.connectors():
        if conn.i2c_bus is None:
            _line(WARN, f"{conn.model} on {conn.name}", "no DDC channel")
            continue
        dev = Path(f"/dev/i2c-{conn.i2c_bus}")
        if not dev.exists():
            _line(BAD, f"{conn.model} on {conn.name}", f"{dev} missing: load i2c-dev (sudo modprobe i2c-dev)")
            problems += 1
        elif not os.access(dev, os.R_OK | os.W_OK):
            _line(BAD, f"{conn.model} on {conn.name}", f"no access to {dev}: run ./install.sh (adds a udev rule)")
            problems += 1
        else:
            _line(OK, f"{conn.model} on {conn.name}", f"{dev} accessible")

    from gi.repository import GLib  # noqa: F401  (ensures GI works)

    from .core.bus import EventBus
    from .platform.display.service import DisplayService

    print("\nBrightness / contrast")
    display = DisplayService(cfg.section("display"), EventBus())
    display.probe_blocking()
    if display.monitors:
        for monitor in display.monitors:
            values = ", ".join(f"{k} {round(v.percent)}%" for k, v in monitor.controls.items())
            _line(OK, f"{monitor.name} via {monitor.backend.name}", values)
    else:
        _line(BAD, "no controllable display", "enable DDC/CI in the monitor's OSD menu; check access above")
        problems += 1
    for problem in display.problems:
        _line(WARN, problem)

    from .core.state import State
    from .platform.display.nightlight import NightLight

    _line(OK if NightLight(State()).available else WARN, "night light", "GNOME" if NightLight(State()).available else "unsupported desktop")

    print("\nAudio & media")
    from .platform.audio.service import pick_backend

    audio = pick_backend(cfg.get("audio.backend", "auto"))
    if audio:
        level = audio.get("sink")
        _line(OK, f"audio via {audio.name}", f"volume {round(level.volume * 100)}%{' (muted)' if level.muted else ''}")
    else:
        _line(BAD, "no audio backend", "install wireplumber (wpctl) or pulseaudio-utils (pactl)")
    from .platform import dbus

    players = [n for n in dbus.list_names(dbus.session()) if n.startswith("org.mpris.MediaPlayer2.")]
    _line(OK, f"{len(players)} MPRIS player(s)", ", ".join(p.split(".")[3] for p in players))
    from .platform.media import mpd

    host, port, password = mpd.target()
    host = str(cfg.get("media.mpd_host") or host)
    port = int(cfg.get("media.mpd_port") or port)
    found_mpd = str(cfg.get("media.mpd", "auto")).lower() not in ("false", "off") and mpd.probe(host, port, password)
    _line(OK if found_mpd else WARN, "MPD", f"{host}:{port}" if found_mpd else "not running (optional)")
    import shutil as _shutil

    _line(OK if _shutil.which("pw-dump") or (audio and audio.name == "pactl") else WARN, "per-app volume",
          "pw-dump" if _shutil.which("pw-dump") else "pactl" if audio and audio.name == "pactl"
          else "install pipewire-bin (pw-dump) for the app sliders")

    print("\nSensors")
    from .platform import sensors

    for kind in ("cpu", "gpu"):
        spec = cfg.get(f"glance.{kind}_sensor", "auto")
        found = sensors.find_sensor(spec, kind)
        if found:
            _line(OK, f"{kind.upper()} temperature", f"{found.chip}/{found.label} = {found.read():.0f}°C")
        else:
            _line(WARN, f"{kind.upper()} temperature", "not found")

    print("\nToggles")
    from .platform.desktop import DarkMode, DoNotDisturb
    from .platform.network import Bluetooth, WiFi
    from .platform.power import Caffeine

    from .platform.network import Airplane, Vpn
    from .platform.power import PowerProfiles

    for name, available in (("Wi-Fi", WiFi.available()), ("Bluetooth", Bluetooth.available()),
                            ("Dark mode", DarkMode().available()), ("Do not disturb", DoNotDisturb().available()),
                            ("Caffeine", Caffeine().available()), ("Power profiles", PowerProfiles().available()),
                            ("VPN", Vpn.available()), ("Airplane mode", Airplane.available())):
        _line(OK if available else WARN, name, "" if available else "hidden (not available here)")

    print("\nBattery")
    from .core.bus import EventBus as _Bus
    from .platform.power.upower import Batteries

    batteries = Batteries(_Bus())
    batteries.start()
    if not batteries.running:
        _line(WARN, "UPower not running", "the Battery card hides itself")
    elif not batteries.current():
        _line(OK, "UPower", "no batteries right now (the card appears when one shows up)")
    for device in batteries.current():
        _line(OK, f"{device.model} ({device.kind})", f"{round(device.percent)}% {device.state}")

    print("\nWindow")
    try:
        import gi

        gi.require_version("Wnck", "3.0")
        from gi.repository import Wnck  # noqa: F401

        _line(OK, "libwnck", "focus hand-back and fullscreen detection")
    except (ImportError, ValueError):
        _line(WARN, "libwnck missing", "install gir1.2-wnck-3.0 for fullscreen hiding")
    if info.is_wayland and not info.is_gnome:
        try:
            import gi

            gi.require_version("GtkLayerShell", "0.1")
            _line(OK, "gtk-layer-shell")
        except ValueError:
            _line(BAD, "gtk-layer-shell missing", "install gir1.2-gtklayershell-0.1")
            problems += 1

    print("\nLiquid Glass")
    from . import native
    from .platform import backdrop

    if native.available():
        _line(OK, "glass kernel", str(native.library_path()))
    else:
        _line(WARN, "glass kernel unavailable", f"{native.error()} (the Glass theme hides itself)")
    source, detail = backdrop.describe()
    _line(OK if source == "x11" else WARN, f"backdrop: {source}", detail)
    _line(OK, "theme", "Liquid Glass" if cfg.get("panel.glass", False) else "black (panel.glass = false)")

    print("\nIntegration")
    autostart = Path.home() / ".config/autostart/cornerwindow.desktop"
    _line(OK if autostart.exists() else WARN, "autostart", str(autostart) if autostart.exists() else "run ./install.sh")
    _line(OK if shutil.which("cornerwindow") else WARN, "cornerwindow on PATH",
          shutil.which("cornerwindow") or "run ./install.sh")
    _line(OK, "data", str(paths.data_dir()))

    print("\n" + ("All good." if not problems else f"{problems} problem(s) found.") + "\n")
    return 1 if problems else 0
