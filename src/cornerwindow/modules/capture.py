"""Screen capture from the command bar: `shot` (area, window, screen), `pick` (a colour from
anywhere on screen), `ocr` (copy the text in an area) and `qr` (read a QR code). A tool: no
card, only commands and actions you can bind to the trigger."""

from __future__ import annotations

from pathlib import Path

from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from ..core.activity import GREEN
from ..platform import capture
from ..platform.desktop import open_uri
from .base import Command, Module, Suggestion, module

DELAY_MS = 350  # let the panel close before the screen is captured


@module("capture", title="Capture", area="tool", icon="screenshot",
        description="Screenshots, colour picker, copy text from the screen, read QR codes")
class Capture(Module):
    def setup(self) -> None:
        for mode in ("area", "window", "screen"):
            self.ctx.actions.register(f"capture.{mode}", lambda m=mode: self.shot(m), f"Screenshot ({mode})")
        self.ctx.actions.register("capture.color", self.pick, "Pick a colour from the screen")
        self.ctx.actions.register("capture.text", self.ocr, "Copy text from an area of the screen")
        self.ctx.actions.register("capture.qr", self.qr, "Read a QR code on screen")

    # -- the work, after the panel is out of the way ------------------------------------------
    def _later(self, fn) -> None:
        self.ctx.island.close()
        GLib.timeout_add(int(self.config.get("delay_ms", DELAY_MS)), lambda: fn() and False)

    def shot(self, mode: str = "area") -> str:
        def done(path: Path | None) -> None:
            if path is None:
                return
            if self.config.get("copy", True):
                _copy_image(path)
            self.ctx.activity.show("capture", dot=True, color=GREEN, timeout=1.2)
            if self.config.get("notify", True):
                self.ctx.notify("Screenshot saved", f"{path.name} · copied to the clipboard"
                                if self.config.get("copy", True) else path.name, "camera-photo-symbolic")

        self._later(lambda: capture.capture(mode, done))
        return f"Screenshot: {mode}…"

    def pick(self) -> str:
        def done(rgb) -> None:
            if rgb is None:
                return
            values = capture.formats(*rgb)
            fmt = str(self.config.get("color_format", "hex"))
            text = values.get(fmt, values["hex"])
            Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)
            self.ctx.activity.show("capture.color", dot=True, color=tuple(rgb), timeout=2.5)
            self.ctx.notify(f"Copied {text}", f"{values['rgb']} · {values['hsl']}", "color-select-symbolic")

        self._later(lambda: capture.pick_color(done))
        return "Click anywhere to pick a colour…"

    def ocr(self) -> str:
        if not capture.can_read_text():
            return "Install tesseract to copy text from the screen"

        def read(path: Path | None) -> None:
            if path is None:
                return
            language = str(self.config.get("ocr_language", ""))

            def work() -> str:
                try:
                    return capture.read_text(path, language)
                finally:
                    path.unlink(missing_ok=True)

            def done(text: str) -> None:
                if not text:
                    self.ctx.notify("No text found", "Try a larger area", "dialog-information-symbolic")
                    return
                Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)
                self.ctx.activity.show("capture", dot=True, color=GREEN, timeout=1.2)
                self.ctx.notify("Text copied", text[:180], "edit-copy-symbolic")

            self.ctx.run_async(work, done)

        self._later(lambda: capture.capture("area", read, keep=False))
        return "Select the text…"

    def qr(self) -> str:
        if not capture.can_read_qr():
            return "Install zbar-tools to read QR codes"

        def read(path: Path | None) -> None:
            if path is None:
                return

            def work() -> list[str]:
                try:
                    return capture.read_qr(path)
                finally:
                    path.unlink(missing_ok=True)

            def done(codes: list[str]) -> None:
                if not codes:
                    self.ctx.notify("No QR code found", "Select the whole code", "dialog-information-symbolic")
                    return
                text = codes[0]
                Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)
                if text.startswith(("http://", "https://")) and self.config.get("open_qr_links", False):
                    open_uri(text)
                self.ctx.notify("QR code copied", text[:180], "edit-copy-symbolic")

            self.ctx.run_async(work, done)

        self._later(lambda: capture.capture("area", read, keep=False))
        return "Select the QR code…"

    def commands(self) -> list[Command]:
        def shot_rows(arg: str) -> list[Suggestion]:
            arg = arg.strip().lower()
            modes = [("area", "Screenshot of an area"), ("window", "Screenshot of a window"),
                     ("screen", "Screenshot of the whole screen")]
            rows = [Suggestion(title, "Saved to Pictures/Screenshots, copied", lambda m=m: self.shot(m),
                               "screenshot", closes=True) for m, title in modes if not arg or m.startswith(arg)]
            rows.append(Suggestion("Open the screenshots folder", "", lambda: open_uri(
                capture.screenshot_folder().as_uri()) or "Opened", "folder", closes=True))
            return rows

        return [
            Command(("shot", "screenshot"), "Screenshot", "shot · shot window · shot screen",
                    lambda a: self.shot((a.strip() or "area").split()[0]), True, suggest=shot_rows),
            Command(("pick", "color"), "Pick a colour from the screen", "pick", lambda _a: self.pick(), icon="color"),
            Command(("ocr",), "Copy text from the screen", "ocr", lambda _a: self.ocr(), icon="ocr"),
            Command(("qr",), "Read a QR code on screen", "qr", lambda _a: self.qr(), icon="qr"),
        ]


def _copy_image(path: Path) -> None:
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(path))
    except GLib.Error:
        return
    clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    clipboard.set_image(pixbuf)
    clipboard.store()
