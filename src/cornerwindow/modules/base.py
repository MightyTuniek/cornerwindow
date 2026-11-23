"""The module API: the one thing you need to read to add a feature.

    from cornerwindow.modules.base import Module, module, Command

    @module("hello", title="Hello")
    class Hello(Module):
        def build(self):
            return Card("Hello").add_label("world")   # any Gtk.Widget

A module may also:
  * available()        -> False hides it (missing hardware, unsupported desktop)
  * setup()            register actions/commands/subscriptions (runs once, even if not built)
  * configure()        read self.config; runs after setup() and again whenever settings change
  * on_open/on_close   panel lifecycle; start/stop work here
  * commands()         command-bar entries, e.g. Command(("b", "brightness"), ...); they're
                       listed in the bar's help (?) automatically
  * self.ctx.every(1.0, fn)   a timer that only runs while the panel is open

Everything a module needs is on `self.ctx` (see ModuleContext).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from gi.repository import GLib

from ..core import tasks
from ..core.activity import ActivityManager
from ..core.actions import ActionRegistry
from ..core.bus import EventBus
from ..core.state import State

if TYPE_CHECKING:
    from ..config import Config
    from ..services import Services
    from ..ui.island import Island


@dataclass
class Suggestion:
    """One live row a command offers for what was typed after its keyword ("cb pass" -> the
    matching clipboard entries). `run()` returns feedback text (or None)."""

    title: str
    detail: str = ""
    run: Callable[[], str | None] = lambda: None
    icon: Any = None  # an icon name or a Gio.Icon
    closes: bool = False  # True: close the panel after running it
    complete: str | None = None  # Tab puts this text in the bar
    confirm: bool = False  # needs Enter twice (stopping a process)


@dataclass
class Command:
    """A command-bar entry. `run(arg)` returns feedback text (or None).

    * usage     examples separated by " · "; the first one fills the bar from the help list
    * suggest   fn(arg) -> [Suggestion]: live rows once the keyword is typed in full
    * confirm   needs Enter twice (restart, shut down)
    * group     heading in the help list; defaults to the module's title
    """

    keywords: tuple[str, ...]
    title: str
    usage: str = ""
    run: Callable[[str], str | None] = lambda _arg: None
    takes_arg: bool = False
    suggest: Callable[[str], list[Suggestion]] | None = None
    confirm: bool = False
    group: str = ""
    icon: str = ""

    def matches(self, word: str) -> bool:
        word = word.lower()
        return any(k.startswith(word) for k in self.keywords)


@dataclass
class ModuleContext:
    id: str
    config: "Config"
    section: dict
    services: "Services"
    bus: EventBus
    actions: ActionRegistry
    activity: ActivityManager
    state: State
    data_dir: Path
    island: "Island"
    host: Any = None  # the ModuleHost, for container modules such as the drawer
    _timers: list = field(default_factory=list)

    # -- helpers ----------------------------------------------------------------------
    def every(self, seconds: float, fn: Callable[[], Any], immediately: bool = True) -> None:
        """Run fn every `seconds` while the panel is open (zero cost when collapsed)."""
        self._timers.append({"seconds": seconds, "fn": fn, "immediately": immediately, "source": 0})

    def run_async(self, fn: Callable[[], Any], then: Callable[[Any], None] | None = None) -> None:
        tasks.run_async(fn, then)

    def notify(self, title: str, body: str = "", icon: str = "dialog-information-symbolic") -> None:
        from ..platform import dbus

        def send() -> None:
            dbus.call(dbus.session(), "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                      "org.freedesktop.Notifications", "Notify",
                      GLib.Variant("(susssasa{sv}i)", ("CornerWindow", 0, icon, title, body, [], {}, -1)),
                      "(u)")

        tasks.run_async(send)

    # -- lifecycle (called by the host) -------------------------------------------------
    def _start_timers(self) -> None:
        for timer in self._timers:
            if timer["source"]:
                continue
            if timer["immediately"]:
                _safe(timer["fn"])

            def tick(t=timer) -> bool:
                _safe(t["fn"])
                return True

            timer["source"] = GLib.timeout_add(int(timer["seconds"] * 1000), tick)

    def _stop_timers(self) -> None:
        for timer in self._timers:
            if timer["source"]:
                GLib.source_remove(timer["source"])
                timer["source"] = 0


def _safe(fn: Callable[[], Any]) -> None:
    try:
        fn()
    except Exception:
        from ..core import log

        log.get("module").exception("Timer callback failed")


class Module:
    id: str = ""
    title: str = ""
    area: str = "main"  # "main" (a card), "drawer" (a tab in the drawer) or "tool" (no card: commands only)
    icon: str = ""
    description: str = ""  # one line, shown on the settings Layout page

    def __init__(self, ctx: ModuleContext) -> None:
        self.ctx = ctx
        self.config = ctx.section

    def available(self) -> bool:
        return True

    def setup(self) -> None:
        """Register actions, commands and bus subscriptions. Runs once."""

    def configure(self) -> None:
        """(Re)read self.config. Runs after setup() and on every settings change, before build()."""

    def build(self):
        if self.area == "tool":
            return None  # tools have no card
        raise NotImplementedError

    def on_open(self) -> None:
        pass

    def on_close(self) -> None:
        pass

    def commands(self) -> list[Command]:
        return []


_REGISTRY: dict[str, type[Module]] = {}


def module(id: str, title: str = "", area: str = "main", icon: str = "", description: str = ""):
    """Class decorator that registers a module under `id`."""

    def wrap(cls: type[Module]) -> type[Module]:
        cls.id, cls.title, cls.area, cls.icon = id, title or id.title(), area, icon
        cls.description = description or cls.description
        _REGISTRY[id] = cls
        return cls

    return wrap


def registered() -> dict[str, type[Module]]:
    return dict(_REGISTRY)
