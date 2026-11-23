"""Text snippets from the command bar: type `;sig` and Enter pastes your signature into the window
you were in (or copies it, where pasting can't work). Placeholders: {date} {time} {datetime}
{weekday} {clipboard} {date:%d.%m.%Y}. Define them under [snippets.items], or add one from the
bar: `sn add name text`. A tool: no card."""

from __future__ import annotations

import json
import os

from gi.repository import Gdk, GLib, Gtk

from ..core import snippets as core
from ..core.activity import GREEN
from ..platform import proc
from .base import Command, Module, Suggestion, module


@module("snippets", title="Snippets", area="tool", icon="snippet",
        description="Short names that expand to text: ;sig pastes your signature")
class Snippets(Module):
    def setup(self) -> None:
        self.file = self.ctx.data_dir / "snippets.json"

    def all(self) -> dict[str, str]:
        out = {str(k): str(v) for k, v in (self.config.get("items", {}) or {}).items()}
        try:
            out.update(json.loads(self.file.read_text()))
        except (OSError, ValueError):
            pass
        return out

    def _save_added(self, name: str, text: str | None) -> None:
        try:
            added = json.loads(self.file.read_text())
        except (OSError, ValueError):
            added = {}
        if text is None:
            added.pop(name, None)
        else:
            added[name] = text
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps(added, indent=1, ensure_ascii=False))

    # -- using one --------------------------------------------------------------------
    def use(self, name: str) -> str:
        text = self.all().get(name)
        if text is None:
            return f"No snippet {name!r}"
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        current = ""
        if core.needs_clipboard(text):  # the history already holds it: no blocking clipboard read
            items = self.ctx.services.clipboard.items
            current = items[0] if items else ""
        value = core.expand(text, clipboard=current)
        clipboard.set_text(value, -1)
        clipboard.store()
        self.ctx.activity.show("snippets", dot=True, color=GREEN, timeout=0.9)
        if self.config.get("paste", True) and self._can_paste():
            self.ctx.island.close()
            GLib.timeout_add(int(self.config.get("paste_delay_ms", 250)), self._paste)
            return f"Pasted {name}"
        return f"Copied {name}"

    @staticmethod
    def _can_paste() -> bool:
        if os.environ.get("WAYLAND_DISPLAY"):
            return proc.which("wtype") is not None
        return proc.which("xdotool") is not None

    def _paste(self) -> bool:
        if os.environ.get("WAYLAND_DISPLAY"):
            proc.spawn(["wtype", "-M", "ctrl", "v", "-m", "ctrl"])
        else:
            proc.spawn(["xdotool", "key", "--clearmodifiers", "ctrl+v"])
        return False

    # -- the bar ----------------------------------------------------------------------
    def commands(self) -> list[Command]:
        snippets = self.all()

        def preview(text: str) -> str:
            return " ".join(core.expand(text).split())[:70]

        def rows(arg: str) -> list[Suggestion]:
            arg = arg.strip()
            if arg.startswith("add "):
                name, _, text = arg[4:].strip().partition(" ")
                if not name or not text:
                    return [Suggestion("Type: sn add name the text", "", lambda: None, "snippet")]
                return [Suggestion(f"Add snippet ;{name}", text[:70], lambda n=name, t=text: self._add(n, t),
                                   "add")]
            if arg.startswith("remove "):
                name = arg[7:].strip()
                return [Suggestion(f"Remove snippet ;{name}", "Only ones added from the bar",
                                   lambda n=name: self._remove(n), "clear")]
            found = core.matching(snippets, arg)
            if not found:
                return [Suggestion("No snippets yet: sn add sig Kind regards", "", lambda: None, "snippet")]
            return [Suggestion(f";{name}", preview(text), lambda n=name: self.use(n), "snippet")
                    for name, text in found[:7]]

        cmds = [Command(("sn", "snippet", "snippets"), "Snippets", "sn · sn sig · sn add sig Kind regards",
                        lambda a: self.use(core.matching(snippets, a)[0][0]) if core.matching(snippets, a)
                        else "No snippet matches", True, suggest=rows)]
        for name, text in snippets.items():  # ";sig" works on its own
            cmds.append(Command((f";{name.lower()}",), f";{name}", preview(text),
                                lambda _a, n=name: self.use(n), group="Snippets"))
        return cmds

    def _add(self, name: str, text: str) -> str:
        self._save_added(name, text.replace("\\n", "\n"))
        return f"Added ;{name}"

    def _remove(self, name: str) -> str:
        self._save_added(name, None)
        return f"Removed ;{name}"
