#!/usr/bin/env python3
"""Regenerate the README screenshots (docs/screenshots/*.png) from the real app in demo mode.

Run it through scripts/screenshots.sh, which provides the virtual screen and session bus:

    scripts/screenshots.sh                  # every picture
    scripts/screenshots.sh panel glass      # only these
    scripts/screenshots.sh --out /tmp/shots # somewhere else

Pictures: panel, glass, commandbar, settings, trigger. Each scene opens the real panel with the
demo's made-up data (src/cornerwindow/demo.py), waits for the springs to settle and draws the
windows offscreen over the demo wallpaper (src/cornerwindow/ui/snapshot.py), at 2x scale.
To add a picture, add a scene function below and list it in SCENES.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

SETTLE = 2.8  # seconds for the open spring and the cards' staggered entrance


# -- scenes: generators that yield how long to wait before the next step --------------------
def scene_panel(app, out: Path):
    app.island.open()
    yield SETTLE
    save(app, out / "panel.png")
    app.island.close()
    yield 1.0


def scene_glass(app, out: Path):
    app.set_glass(True)
    yield 1.5  # the config reload
    app.island.open()
    yield SETTLE + 1.0  # glass catches up at full resolution once settled
    save(app, out / "glass.png")
    app.island.close()
    yield 0.8
    app.set_glass(False)
    yield 1.5


def scene_commandbar(app, out: Path):
    from cornerwindow.ui import snapshot

    app.island.open()
    yield SETTLE
    bar = app.host.instances["commandbar"]
    bar._set_help(True)
    yield 0.8
    island = app.island
    rect = island.panel_rect()
    _x, y = bar.revealer.translate_coordinates(island, 0, 0)
    bottom = y + bar.revealer.get_allocated_height()
    ox, oy = snapshot.origin(island)
    save(app, out / "commandbar.png", crop=(rect.x, oy, rect.width, bottom + 2))
    bar.reset()
    app.island.close()
    yield 1.0


def scene_settings(app, out: Path):
    from cornerwindow.ui import snapshot

    app.island.open()
    yield SETTLE
    app.open_settings("modules")
    yield 2.0
    win = app.settings
    island = app.island
    panel = island.panel_rect()
    sx, sy = snapshot.origin(win)
    left = max(0, sx - 40)
    top = panel.y
    width = panel.x + panel.width - left
    height = min(max(panel.height, sy + win.get_allocated_height() - top) + 40,
                 snapshot.screen_size()[1] - top)
    save(app, out / "settings.png", extra=[win], crop=(left, top, width, height))
    app.toggle_settings()
    app.island.close()
    yield 1.2


def scene_trigger(app, out: Path):
    """Four states of the collapsed corner side by side, with captions."""
    import cairo

    from cornerwindow.core.activity import ORANGE, WHITE, YELLOW
    from cornerwindow.ui import snapshot

    activity = app.activity
    timer = app.host.instances.get("drawer") and app.host.get("timer")
    sw, _sh = snapshot.screen_size()
    tile_w, tile_h = 120, 84
    states = [
        ("Music playing", lambda: None),
        ("Scroll: brightness", lambda: activity.show("display.brightness", level=0.8, label="80", color=YELLOW,
                                                     timeout=30)),
        ("Shift+scroll: volume", lambda: activity.show("audio.volume", level=0.3, label="30", color=WHITE,
                                                       timeout=30)),
        ("Timer running", lambda: timer.start(25 * 60) if timer else None),
    ]
    tiles = []
    for caption, show in states:
        for key in ("display.brightness", "audio.volume"):
            activity.clear(key)
        if caption != "Timer running" and timer is not None:
            timer.cancel()
        show()
        yield 1.4
        tiles.append((caption, snapshot.compose([(snapshot.render(app.island), *snapshot.origin(app.island))],
                                                (sw - tile_w, 0, tile_w, tile_h), 2, snapshot.wallpaper())))
    if timer is not None:
        timer.cancel()
    activity.clear("timer")

    scale, gap, caption_h = 2, 14, 30
    width = (len(tiles) * tile_w + (len(tiles) - 1) * gap) * scale
    height = (tile_h + caption_h) * scale
    sheet = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    cr = cairo.Context(sheet)
    for i, (caption, tile) in enumerate(tiles):
        x = i * (tile_w + gap) * scale
        cr.save()
        _rounded(cr, x, 0, tile_w * scale, tile_h * scale, 12 * scale)
        cr.clip()
        cr.set_source_surface(tile, x, 0)
        cr.paint()
        cr.restore()
        _caption(cr, caption, x + tile_w * scale / 2, (tile_h + 22) * scale, 14 * scale)
    sheet.write_to_png(str(out / "trigger.png"))
    print(f"  wrote {out / 'trigger.png'}", flush=True)
    yield 0.2


SCENES = {"panel": scene_panel, "glass": scene_glass, "commandbar": scene_commandbar,
          "settings": scene_settings, "trigger": scene_trigger}


# -- helpers -------------------------------------------------------------------------------
def save(app, path: Path, **kw) -> None:
    from cornerwindow.ui import snapshot

    snapshot.save_island(app, str(path), **kw)
    print(f"  wrote {path}", flush=True)


def _rounded(cr, x, y, w, h, r) -> None:
    import math

    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _caption(cr, text: str, cx: float, baseline: float, size: float) -> None:
    import gi

    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Pango, PangoCairo

    layout = PangoCairo.create_layout(cr)
    layout.set_font_description(Pango.FontDescription(f"Inter, Sans {size / 1.333:.0f}px"))
    layout.set_text(text, -1)
    ink, logical = layout.get_pixel_extents()
    cr.move_to(cx - logical.width / 2, baseline - logical.height)
    cr.set_source_rgba(0.55, 0.57, 0.62, 1)  # mid grey: reads on GitHub's light and dark pages
    PangoCairo.show_layout(cr, layout)


def run(names: list[str], out: Path) -> int:
    from gi.repository import GLib

    from cornerwindow import demo

    demo.isolate()
    from cornerwindow.app import CornerWindowApp

    app = CornerWindowApp()
    status = {"code": 0}

    def steps():
        for name in names:
            print(f"{name}:", flush=True)
            yield from SCENES[name](app, out)

    def start() -> bool:
        seed_history(app)
        sequence = steps()

        def advance() -> bool:
            try:
                wait = next(sequence)
            except StopIteration:
                app.quit()
                return False
            except Exception:
                import traceback

                traceback.print_exc()
                status["code"] = 1
                app.quit()
                return False
            GLib.timeout_add(int(wait * 1000), advance)
            return False

        advance()
        return False

    GLib.timeout_add(2500, start)  # let the app build, probe and read the calendar first
    app.run(["cornerwindow"])
    return status["code"]


def seed_history(app) -> None:
    """Fill the System card's sparklines, as if the panel had been open for a while."""
    import math

    glance = app.host.instances.get("glance")
    if glance is None:
        return
    for i in range(40):
        glance.history["cpu"].append(9 + 5 * math.sin(i / 3) + 3 * math.sin(i * 1.7))
        glance.history["gpu"].append(45 + 8 * math.sin(i / 4 + 1) + 4 * math.sin(i * 1.3))
        glance.history["mem"].append(30 + 0.3 * math.sin(i / 6))
        glance.history["net"].append(2_400_000 + 1_100_000 * math.sin(i / 2.5) + 500_000 * math.sin(i * 1.9))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("names", nargs="*", help=f"pictures to make: {', '.join(SCENES)} (default: all)")
    parser.add_argument("--out", type=Path, default=REPO / "docs" / "screenshots")
    args = parser.parse_args()
    unknown = [n for n in args.names if n not in SCENES]
    if unknown:
        parser.error(f"unknown picture(s): {', '.join(unknown)}")
    args.out.mkdir(parents=True, exist_ok=True)
    os.environ["CORNERWINDOW_DEMO"] = "1"
    os.environ.setdefault("CORNERWINDOW_DEMO_HOME", tempfile.mkdtemp(prefix="cornerwindow-demo-"))
    os.environ.setdefault("GDK_SCALE", "2")
    os.environ.setdefault("NO_AT_BRIDGE", "1")
    names = args.names or list(SCENES)
    status = run(names, args.out.resolve())
    if {"panel", "glass"} <= set(names):
        status = check_same_size(args.out / "panel.png", args.out / "glass.png") or status
    return status


def check_same_size(a: Path, b: Path) -> int:
    """Switching black <-> glass must never move anything (DEVELOPING.md): same picture size."""
    import struct

    sizes = [struct.unpack(">II", path.read_bytes()[16:24]) for path in (a, b)]
    if sizes[0] != sizes[1]:
        print(f"WARNING: {a.name} is {sizes[0]} but {b.name} is {sizes[1]}: the glass theme changes the "
              "panel's size (a glass CSS rule without its 1px-less padding twin?)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
