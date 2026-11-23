"""Window layouts from the command bar: `snap left`, `snap tr`, `snap max`, `snap center`,
`snap next` (the next monitor), `snap restore`. Each is also an action, so a desktop shortcut can
run `cornerwindow action window.snap.left`. X11 only (Wayland compositors don't let other apps
move windows). A tool: no card."""

from __future__ import annotations

from ..core import snap
from .base import Command, Module, Suggestion, module

ROWS = [("left", "Left half"), ("right", "Right half"), ("top", "Top half"), ("bottom", "Bottom half"),
        ("top-left", "Top-left quarter"), ("top-right", "Top-right quarter"),
        ("bottom-left", "Bottom-left quarter"), ("bottom-right", "Bottom-right quarter"),
        ("left-third", "Left third"), ("center-third", "Middle third"), ("right-third", "Right third"),
        ("left-two-thirds", "Left two thirds"), ("right-two-thirds", "Right two thirds"),
        ("max", "Maximise"), ("center", "Centre"), ("next-monitor", "Move to the next monitor"),
        ("restore", "Back to where it was")]


@module("windowtools", title="Window layouts", area="tool", icon="window",
        description="Snap the window you were in to halves, quarters, thirds or the next monitor (X11)")
class WindowTools(Module):
    def setup(self) -> None:
        self.windows = self.ctx.services.windows
        for name, title in ROWS:
            self.ctx.actions.register(f"window.snap.{name}", lambda n=name: self.place(n), title)

    def available(self) -> bool:
        return self.windows.available()

    def place(self, name: str) -> str:
        self.ctx.island.close()
        return self.windows.snap(name, int(self.config.get("gap", 0)))

    def commands(self) -> list[Command]:
        def run(arg: str) -> str:
            name = snap.resolve(arg or "")
            if name is None:
                return "Usage: snap left · snap tr · snap max · snap next"
            return self.place(name)

        def rows(arg: str) -> list[Suggestion]:
            target = self.windows.target()
            where = f"Moves {target.get_name()}" if target is not None else "No window to move"
            arg = arg.strip().lower()
            exact = snap.resolve(arg) if arg else None
            picked = [(n, t) for n, t in ROWS if not arg or n == exact or n.startswith(arg) or arg in t.lower()]
            return [Suggestion(title, where, lambda n=n: self.place(n), "window", closes=True)
                    for n, title in picked[:7]]

        return [Command(("snap", "win"), "Move the window you were in", "snap left · snap tr · snap max · snap next",
                        run, True, suggest=rows)]
