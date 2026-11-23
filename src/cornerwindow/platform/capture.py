"""Screen capture: screenshots, a colour picker, text recognition and QR codes.

The xdg-desktop-portal comes first (GNOME, KDE and wlroots desktops all answer it, X11 and
Wayland alike); common tools are the fallback: gnome-screenshot, spectacle, flameshot, maim,
scrot, grim + slurp; xcolor / hyprpicker / gpick for colours. Text and QR codes are read
offline by `tesseract` and `zbarimg` when they're installed.

Everything here calls back on the main thread; the colour maths is pure, for the tests.
"""

from __future__ import annotations

import colorsys
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

from ..core import log, tasks
from . import proc

_log = log.get("capture")
PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"


# -- colours -------------------------------------------------------------------------------
def formats(r: float, g: float, b: float) -> dict[str, str]:
    """0..1 floats -> hex, rgb() and hsl() strings."""
    R, G, B = (max(0, min(255, round(c * 255))) for c in (r, g, b))
    h, l, s = colorsys.rgb_to_hls(R / 255, G / 255, B / 255)
    return {
        "hex": f"#{R:02x}{G:02x}{B:02x}",
        "rgb": f"rgb({R}, {G}, {B})",
        "hsl": f"hsl({round(h * 360)}, {round(s * 100)}%, {round(l * 100)}%)",
    }


def parse_color(text: str) -> tuple[float, float, float] | None:
    """What colour tools print: '#1a2b3c', '1a2b3c', 'rgb(1, 2, 3)'."""
    text = text.strip()
    match = re.search(r"#?([0-9a-fA-F]{6})\b", text)
    if match:
        value = match.group(1)
        return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]
    match = re.search(r"(\d{1,3})\D+(\d{1,3})\D+(\d{1,3})", text)
    if match:
        return tuple(min(255, int(v)) / 255 for v in match.groups())  # type: ignore[return-value]
    return None


# -- the portal ----------------------------------------------------------------------------
def _portal_request(method: str, interface: str, args_sig: str, args: tuple, on_response: Callable[[int, dict], None]) -> bool:
    """Call a portal method that answers with a Request.Response signal."""
    from gi.repository import Gio, GLib

    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except Exception:
        return False
    token = "cw" + secrets.token_hex(6)
    sender = bus.get_unique_name().lstrip(":").replace(".", "_")
    handle = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
    state = {"sub": 0}

    def response(_conn, _sender, _path, _iface, _signal, params) -> None:
        bus.signal_unsubscribe(state["sub"])
        code, results = params.unpack()
        tasks.on_main(on_response, code, results)

    state["sub"] = bus.signal_subscribe(PORTAL, "org.freedesktop.portal.Request", "Response", handle, None,
                                        Gio.DBusSignalFlags.NO_MATCH_RULE, response)
    options = dict(args[-1])
    options["handle_token"] = GLib.Variant("s", token)
    variant = GLib.Variant(args_sig, (*args[:-1], options))

    def failed(_source, result) -> None:
        try:
            bus.call_finish(result)
        except Exception as exc:
            _log.info("Portal %s failed: %s", method, exc)
            bus.signal_unsubscribe(state["sub"])
            tasks.on_main(on_response, 2, {})

    bus.call(PORTAL, PORTAL_PATH, interface, method, variant, GLib.VariantType.new("(o)"),
             Gio.DBusCallFlags.NONE, 120000, None, failed)
    return True


def portal_available() -> bool:
    from . import dbus

    try:
        return PORTAL in dbus.list_names(dbus.session())
    except Exception:
        return False


# -- screenshots ---------------------------------------------------------------------------
def screenshot_folder() -> Path:
    from gi.repository import GLib

    pictures = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES) or str(Path.home() / "Pictures")
    folder = Path(pictures) / "Screenshots"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def new_name(folder: Path) -> Path:
    return folder / time.strftime("Screenshot %Y-%m-%d %H-%M-%S.png")


def tool_command(mode: str, target: Path) -> list[str] | None:
    """A screenshot tool that writes `target`. mode = area | window | screen."""
    wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    t = str(target)
    options: list[tuple[str, dict[str, list[str]]]] = [
        ("gnome-screenshot", {"area": ["-a", "-f", t], "window": ["-w", "-f", t], "screen": ["-f", t]}),
        ("spectacle", {"area": ["-r", "-b", "-n", "-o", t], "window": ["-a", "-b", "-n", "-o", t],
                       "screen": ["-f", "-b", "-n", "-o", t]}),
        ("flameshot", {"area": ["gui", "-p", t], "screen": ["full", "-p", t]}),
    ]
    if not wayland:
        options += [("maim", {"area": ["-s", t], "window": ["-i", "$ACTIVE", t], "screen": [t]}),
                    ("scrot", {"area": ["-s", t], "window": ["-u", t], "screen": [t]})]
    for tool, modes in options:
        if mode in modes and proc.which(tool):
            args = modes[mode]
            if "$ACTIVE" in args:
                active = proc.run(["xdotool", "getactivewindow"], 2).strip() if proc.which("xdotool") else ""
                if not active:
                    continue
                args = [active if a == "$ACTIVE" else a for a in args]
            return [tool, *args]
    if wayland and proc.which("grim"):
        if mode == "area" and proc.which("slurp"):
            return ["sh", "-c", f'grim -g "$(slurp)" {shlex_quote(t)}']
        if mode == "screen":
            return ["grim", t]
    return None


def shlex_quote(text: str) -> str:
    import shlex

    return shlex.quote(text)


def capture(mode: str, then: Callable[[Path | None], None], keep: bool = True) -> None:
    """Take a screenshot (area / window / screen). `then(path)` gets the saved PNG, or None when it
    was cancelled or nothing can take screenshots here. keep=False writes to a temporary file
    (for text recognition and QR codes). The portal goes first; a tool is the fallback."""
    from gi.repository import GLib

    target = new_name(screenshot_folder()) if keep else Path(tempfile.mkstemp(suffix=".png")[1])
    command = tool_command(mode, target)

    def with_tool() -> None:
        if command is None:
            then(None)
            return

        def run() -> Path | None:
            try:
                subprocess.run(command, timeout=120, check=False, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            except (OSError, subprocess.SubprocessError):
                return None
            return target if target.exists() and target.stat().st_size > 0 else None

        tasks.run_async(run, then)

    def from_portal(code: int, results: dict) -> None:
        uri = str(results.get("uri", ""))
        if code == 1:
            then(None)  # cancelled
            return
        if code != 0 or not uri:
            with_tool()
            return
        from urllib.parse import unquote, urlparse

        source = Path(unquote(urlparse(uri).path))
        try:
            shutil.move(str(source), target)
            then(target)
        except OSError:
            then(source if source.exists() else None)

    if portal_available() and _portal_request(
            "Screenshot", "org.freedesktop.portal.Screenshot", "(sa{sv})",
            ("", {"interactive": GLib.Variant("b", mode != "screen")}), from_portal):
        return
    with_tool()


# -- colour picker -------------------------------------------------------------------------
def pick_color(then: Callable[[tuple[float, float, float] | None], None]) -> None:
    def from_portal(code: int, results: dict) -> None:
        color = results.get("color")
        if code == 0 and color:
            then(tuple(float(c) for c in color))  # type: ignore[arg-type]
        elif code == 1:
            then(None)  # cancelled
        else:
            _pick_with_tool(then)

    if portal_available() and _portal_request("PickColor", "org.freedesktop.portal.Screenshot", "(sa{sv})",
                                              ("", {}), from_portal):
        return
    _pick_with_tool(then)


def _pick_with_tool(then) -> None:
    for command in (["hyprpicker", "-n", "-f", "hex"], ["xcolor", "-f", "hex"], ["gpick", "--single", "--output"],
                    ["colorpicker", "--one-shot", "--short"]):
        if proc.which(command[0]):
            def run(c=command):
                out = subprocess.run(c, capture_output=True, text=True, timeout=120).stdout
                return parse_color(out)

            tasks.run_async(run, then, error=lambda _e: then(None))
            return
    then(None)


# -- reading the screen --------------------------------------------------------------------
def can_read_text() -> bool:
    return proc.which("tesseract") is not None


def can_read_qr() -> bool:
    return proc.which("zbarimg") is not None


def read_text(image: Path, language: str = "") -> str:
    command = ["tesseract", str(image), "-", "--psm", "6"]
    if language:
        command += ["-l", language]
    out = subprocess.run(command, capture_output=True, text=True, timeout=60).stdout
    return clean_ocr(out)


def clean_ocr(text: str) -> str:
    """Join hard-wrapped lines into paragraphs, keep blank-line breaks; drop form feeds."""
    paragraphs = [" ".join(p.split()) for p in re.split(r"\n\s*\n", text.replace("\f", ""))]
    return "\n\n".join(p for p in paragraphs if p)


def read_qr(image: Path) -> list[str]:
    out = subprocess.run(["zbarimg", "-q", "--raw", str(image)], capture_output=True, text=True, timeout=30).stdout
    return [line for line in out.splitlines() if line.strip()]
