"""The GTK application: builds services, modules and the island; handles CLI commands.

Single instance: running `cornerwindow toggle` (or the hotkey) talks to the already
running process over D-Bus instead of starting a second copy.
"""

from __future__ import annotations

import json
from pathlib import Path

from gi.repository import Gdk, Gio, GLib, Gtk

from . import APP_ID, config
from .config_edit import UserConfigWriter, file_hash
from .core import log, paths
from .core.activity import ActivityManager
from .core.actions import ActionRegistry
from .core.bus import EventBus
from .core.state import State
from .modules.base import ModuleContext
from .modules.registry import ModuleHost, load_user_modules
from .platform.display import drm
from .platform.window import pick_backend
from .services import Services
from .ui.island import Island

_log = log.get("app")

HELP = """\
usage: cornerwindow [command]

  run                      start CornerWindow (default)
  toggle | open | close    control the island
  set brightness|contrast|volume N   set a value (N or +N / -N)
  get brightness|contrast|volume     print a value
  action NAME              run a named action (see `cornerwindow actions`)
  actions                  list named actions
  settings [PAGE]          open the settings window (e.g. `settings display`)
  reload                   re-read the config files
  quit                     stop the running instance
  doctor                   diagnose this machine (no GUI needed)
"""


class CornerWindowApp(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.island: Island | None = None
        self.settings = None
        self._reload_id = 0
        self._reload_then: list = []
        self._monitors: list = []
        self._known_hashes: dict[str, str] = {}
        for name, handler in (("toggle", lambda: self.island and self.island.toggle()),
                              ("open", lambda: self.island and self.island.open()),
                              ("close", lambda: self.island and self.island.close()),
                              ("quit", self.quit)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_a, h=handler: h())
            self.add_action(action)

    # -- construction ---------------------------------------------------------------------
    def _build(self) -> None:
        self.facts = config.gather_facts(drm.monitor_models())
        self.config = config.load(self.facts)
        self.writer = UserConfigWriter(self.config.user_file or paths.user_config_file())
        _log.info("Config: %s", " + ".join(p.name for p in self.config.sources))
        self._load_css()

        self.bus = EventBus()
        self.state = State()
        self.activity = ActivityManager()
        self.actions = ActionRegistry()
        self.services = Services(self.config, self.bus, self.state)
        backend = pick_backend()
        _log.info("Window backend: %s", backend.name)

        island = Island(self.config, backend, self.activity, self.actions)
        self.island = island
        self.add_window(island)

        host = ModuleHost(self._context)
        self.host = host
        load_user_modules([paths.user_modules_dir(),
                           *[Path(p) for p in self.config.get("modules.search_paths", [])]])

        self.actions.register("settings.toggle", self.toggle_settings, "Open / close settings")
        self._layout()

        island.on_open_hooks.append(host.opened)
        island.on_close_hooks.append(host.closed)
        island.on_close_hooks.append(lambda: self.settings and self.settings.hide_animated())
        island.keep_open.append(lambda: bool(self.settings and self.settings.shown))
        island.place()
        island.show()
        island._frame()
        backend.watch_fullscreen(island, island.set_fullscreen_hidden)
        self._watch_files()
        self.hold()

    def _layout(self) -> None:
        """Fill the island's grid from [modules] (on startup and after every config change)."""
        island, host = self.island, self.host
        for module_id in self.config.get("modules.top", ["commandbar"]):
            widget = host.build(module_id)
            if widget is not None:
                island.grid.top.pack_start(widget, False, False, 0)
        for column_ids in self.config.get("modules.columns", []):
            column = island.grid.add_column()
            for module_id in column_ids:
                widget = host.build(module_id)
                if widget is not None:
                    island.grid.add_card(column, widget)

    # -- settings & live reload -----------------------------------------------------------
    def toggle_settings(self) -> None:
        if self.settings is None:
            from .ui.settings.window import SettingsWindow

            self.settings = SettingsWindow(self)
            self.add_window(self.settings)
        self.settings.toggle()

    def schedule_reload(self, then=None, external: bool = False, delay: int = 250) -> None:
        """Debounced: many quick edits (dragging a number) cause one reload."""
        if then:
            self._reload_then.append(then)
        self._reload_external = getattr(self, "_reload_external", False) or external
        if self._reload_id:
            GLib.source_remove(self._reload_id)
        self._reload_id = GLib.timeout_add(delay, self._reload)

    def _reload(self) -> bool:
        self._reload_id = 0
        external, self._reload_external = self._reload_external, False
        callbacks, self._reload_then = self._reload_then, []
        try:
            fresh = config.load(self.facts)
        except Exception:
            _log.exception("Config reload failed; keeping the old one")
            return False
        self.config = fresh
        self.services.reconfigure(fresh)
        self.host.reconfigure(fresh)
        self.island.apply_config(fresh)
        self.island.new_grid()
        self._layout()
        self.island.relayout()
        if self.island.is_open:
            self.host.opened()  # restart per-module timers for the freshly built cards
        self._watch_files()
        if self.settings is not None:
            self.settings.after_reload(external)
        for callback in callbacks:
            try:
                callback()
            except Exception:
                _log.exception("Reload callback failed")
        _log.info("Config reloaded%s", " (edited outside)" if external else "")
        return False

    def _watch_files(self) -> None:
        """Watch every config layer + the machines folder; edits made elsewhere apply live."""
        for monitor in self._monitors:
            monitor.cancel()
        self._monitors = []
        targets = {str(p) for p in self.config.sources}
        targets.add(str(self.writer.path))
        self.writer.path.parent.mkdir(parents=True, exist_ok=True)  # so creating the file is noticed
        for target in sorted(targets):
            monitor = Gio.File.new_for_path(target).monitor_file(Gio.FileMonitorFlags.NONE, None)
            monitor.connect("changed", self._file_changed)
            self._monitors.append(monitor)
            self._known_hashes.setdefault(target, _hash_of(target))
        machines = Path(self.config.sources[0]).parent / "machines"
        if machines.is_dir():
            monitor = Gio.File.new_for_path(str(machines)).monitor_directory(Gio.FileMonitorFlags.NONE, None)
            monitor.connect("changed", self._file_changed)
            self._monitors.append(monitor)

    def _file_changed(self, _monitor, file, _other, event) -> None:
        if event not in (Gio.FileMonitorEvent.CHANGES_DONE_HINT, Gio.FileMonitorEvent.CREATED,
                         Gio.FileMonitorEvent.DELETED, Gio.FileMonitorEvent.MOVED_IN,
                         Gio.FileMonitorEvent.RENAMED):
            return
        path = file.get_path() or ""
        if not path.endswith(".toml"):
            return
        digest = _hash_of(path)
        if digest == self._known_hashes.get(path):
            return  # nothing actually changed (e.g. a touch)
        self._known_hashes[path] = digest
        if path == str(self.writer.path) and digest == self.writer.last_written_hash:
            return  # our own save; the settings window already scheduled a reload
        self.schedule_reload(external=True, delay=300)

    def _context(self, module_id: str) -> ModuleContext:
        return ModuleContext(
            id=module_id, config=self.config, section=self.config.section(module_id),
            services=self.services, bus=self.bus, actions=self.actions, activity=self.activity,
            state=self.state, data_dir=paths.data_dir(), island=self.island, host=self.host,
        )

    def _load_css(self) -> None:
        screen = Gdk.Screen.get_default()
        provider = Gtk.CssProvider()
        provider.load_from_path(str(Path(__file__).parent / "ui" / "style.css"))
        Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        user_css = paths.user_config_dir() / "style.css"
        if user_css.exists():
            custom = Gtk.CssProvider()
            try:
                custom.load_from_path(str(user_css))
                Gtk.StyleContext.add_provider_for_screen(screen, custom, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
            except GLib.Error as exc:
                _log.warning("Ignoring %s: %s", user_css, exc.message)

    # -- command line ---------------------------------------------------------------------
    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        args = command_line.get_arguments()[1:]
        if self.island is None:
            self._build()
        try:
            output, status = self._dispatch(args)
        except Exception as exc:  # report to the caller, keep running
            _log.exception("Command %s failed", args)
            output, status = f"error: {exc}", 1
        if output:
            command_line.print_literal(output + "\n")
        return status

    def _dispatch(self, args: list[str]) -> tuple[str, int]:
        island = self.island
        cmd = args[0] if args else "run"
        if cmd == "run":
            return "", 0
        if cmd == "settings":
            if not self.island.is_open:
                self.island.open()
            page = args[1] if len(args) > 1 else None
            if page and self.settings is not None and self.settings.shown:
                self.settings.show_page(page)
            else:
                self.toggle_settings()
                if page:
                    self.settings.show_page(page)
            return "", 0
        if cmd == "reload":
            self.schedule_reload(external=True, delay=0)
            return "", 0
        if cmd in ("toggle", "open", "close"):
            getattr(island, cmd)()
            return "", 0
        if cmd == "quit":
            GLib.idle_add(self.quit)
            return "", 0
        if cmd == "actions":
            return "\n".join(self.actions.names()), 0
        if cmd == "action" and len(args) > 1:
            result = self.actions.run(args[1])
            return (str(result) if result else ""), 0
        if cmd in ("set", "get") and len(args) >= 2:
            return self._value(cmd, args[1], args[2] if len(args) > 2 else None)
        return HELP, 2 if cmd not in ("help", "-h", "--help") else 0

    def _value(self, verb: str, what: str, value: str | None) -> tuple[str, int]:
        if what in ("brightness", "contrast"):
            display = self.services.display
            if not display.ready.wait(timeout=10):
                return "displays are still being detected", 1
            if verb == "get":
                current = display.percent(what)
                return (json.dumps(round(current)) if current is not None else "unavailable"), 0
            if value is None:
                return f"usage: cornerwindow set {what} N", 2
            delta = value.startswith(("+", "-"))
            result = display.step(what, float(value), "cli") if delta else display.set_percent(what, float(value), "cli")
            if result is None:
                return "unavailable", 1
            from .core.activity import YELLOW

            self.activity.show(f"display.{what}", level=result / 100, label=str(round(result)),
                               color=YELLOW if what == "brightness" else (1, 1, 1))
            return str(round(result)), 0
        if what == "volume":
            audio = self.services.audio
            if not audio.available:
                return "unavailable", 1
            if verb == "get":
                return str(round(audio.levels["sink"].volume * 100)), 0
            delta = value.startswith(("+", "-")) if value else False
            result = audio.step("sink", float(value), "cli") if delta else audio.set_volume("sink", float(value) / 100, "cli")
            return str(round(result * 100)), 0
        return f"unknown setting {what!r}", 2


def _hash_of(path: str) -> str:
    try:
        return file_hash(Path(path).read_text())
    except OSError:
        return ""
