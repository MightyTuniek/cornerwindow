"""Start typing anywhere in the open island. One bar for apps, windows, files, folders, websites,
shell commands, every module's commands, a calculator (units, currencies, bases, percentages),
times and dates, emoji, and a web search as the last resort. The ? button lists all of it."""

from __future__ import annotations

import datetime as dt
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from ..core import calc, clock, cmdline, emoji, log, tasks
from ..core.history import History
from ..core.launcher import AppEntry, rank_apps
from ..platform import files, proc
from ..platform.currency import Rates
from ..ui.widgets import label
from ..ui.widgets.card import resolve_icon, set_active
from .base import Command, Module, Suggestion, module

_log = log.get("commandbar")
MAX_RESULTS = 7
HELP_HEIGHT = 260
_CALC_ERRORS = (ValueError, SyntaxError, ZeroDivisionError, TypeError, OverflowError, MemoryError)


@dataclass
class Result:
    title: str
    detail: str
    run: Callable[[], str | None]
    icon: Any = None  # a Gio.Icon, or an icon name
    closes: bool = False  # launching an app or a page closes the island
    complete: str | None = None  # Tab puts this in the bar
    alt: Callable[[], str | None] | None = None  # Shift+Enter
    alt_closes: bool | None = None  # None: same as `closes`
    confirm: bool = False  # needs Enter twice
    key: str = ""  # what history remembers it as ("app:firefox.desktop")
    answer: bool = False  # a calculator/time answer: shown bigger


class AppIndex:
    """Installed apps (.desktop files), loaded off the main thread, refreshed when they change."""

    def __init__(self) -> None:
        self.entries: list[AppEntry] = []
        self._loading = False
        self._stale = True
        try:
            self._monitor = Gio.AppInfoMonitor.get()
            self._monitor.connect("changed", lambda *_: setattr(self, "_stale", True))
        except Exception:
            self._monitor = None

    def ensure(self, then: Callable[[], None] | None = None) -> None:
        if not self._stale or self._loading:
            return
        self._loading = True

        def load() -> list[AppEntry]:
            out = []
            for info in Gio.AppInfo.get_all():
                if not info.should_show():
                    continue
                keywords = []
                if isinstance(info, Gio.DesktopAppInfo):
                    keywords += list(info.get_keywords() or [])
                    keywords.append(info.get_generic_name() or "")
                keywords.append((info.get_executable() or "").rsplit("/", 1)[-1])
                out.append(AppEntry(info.get_display_name() or info.get_name() or "", keywords, info))
            return out

        def done(entries: list[AppEntry]) -> None:
            self.entries, self._loading, self._stale = entries, False, False
            if then:
                then()

        tasks.run_async(load, done, error=lambda _e: setattr(self, "_loading", False))

    def by_id(self, app_id: str) -> AppEntry | None:
        return next((e for e in self.entries if e.ref.get_id() == app_id), None)


DEFAULT_ENGINES = {
    "g": "https://www.google.com/search?q={}",
    "yt": "https://www.youtube.com/results?search_query={}",
    "w": "https://en.wikipedia.org/wiki/Special:Search?search={}",
    "gh": "https://github.com/search?q={}",
    "maps": "https://www.openstreetmap.org/search?query={}",
}

# The help list (?): (heading, [(example, what it does)]). Module commands are added after these.
GUIDE: list[tuple[str, list[tuple[str, str]]]] = [
    ("Open", [
        ("firefox", "Open an app (the ones you use most come first)"),
        ("term", "Switch to an open window by app or title"),
        ("~/Downloads", "Open a file or folder; Tab completes the path"),
        ("github.com", "Open a website"),
        ("f invoice", "Find files by name; Shift+Enter opens the folder"),
    ]),
    ("Run", [
        ("> ls ~", "Run a shell command; the result shows here"),
        (">> htop", "Run a command in a terminal"),
        ("htop", "Programs on your PATH offer to run in a terminal"),
    ]),
    ("Calculate", [
        ("2^10 / 3", "Maths: + − × ÷ ^ ( ), sqrt sin log round …"),
        ("15% of 80", "Percentages, also 80 + 15% and 80 − 15%"),
        ("0xff to bin", "Hex, binary, octal; bit maths like 1<<4, 0xff & 0x0f"),
        ("5 km to mi", "Length, mass, volume, area, speed, data, time"),
        ("70 f to c", "Temperatures (C, F, K)"),
        ("100 usd to eur", "Currencies (daily ECB rates, fetched when you ask)"),
    ]),
    ("Time and dates", [
        ("time in tokyo", "The time anywhere (cities, countries, PST, CET …)"),
        ("3pm london to tokyo", "Convert a time between zones"),
        ("now + 90m", "Date maths, also tomorrow + 2w, dec 25 - 10 days"),
        ("days until dec 25", "Countdowns, also since 2020-01-01"),
        ("today", "Date, week and day of the year; also week, unix, @1700000000"),
    ]),
    ("Text", [
        (":heart", "Emoji and symbols (→ ° ± ™ …); Enter copies"),
    ]),
]
KEYS = [
    ("?", "Show or hide this list"),
    ("↑  ↓", "Move through results. ↑ in an empty bar brings back what you ran before"),
    ("Tab", "Complete a path, or move to the next result"),
    ("Enter", "Run the highlighted row (on an answer: copy it)"),
    ("Shift+Enter", "The alternative: open the containing folder, run in a terminal"),
    ("Esc", "Close this list, then clear the bar, then close the panel"),
]


@module("commandbar", title="Command bar", icon="search",
        description="Type to open apps, files and sites, run commands, calculate and convert")
class CommandBar(Module):
    def setup(self) -> None:
        self.ctx.island.on_type = self.activate
        self.ctx.island.on_close_hooks.append(self.reset)
        self.ctx.island.on_open_hooks.append(self._opened)
        self.ctx.island.on_escape.append(self._escape)
        self._matches: list[Result] = []
        self._selected = 0
        self._feedback_id = 0
        self._armed: Result | None = None
        self._armed_id = 0
        self._gen = 0  # bumps on every keystroke: late async results for older text are dropped
        self._history_pos = -1
        self._filling = False
        self._apps = AppIndex()
        self.history = History(self.ctx.data_dir / "commandbar-history.json")
        self.rates = Rates(self.ctx.data_dir / "exchange-rates.json", "")

    def configure(self) -> None:
        self.rates.url = str(self.config.get("currency_url", "https://api.frankfurter.dev/v1/latest?base=EUR") or "")
        engines = self.config.get("engines", DEFAULT_ENGINES)
        self.engines = {str(k).lower(): str(v) for k, v in (engines or {}).items() if v}
        self.always_show = bool(self.config.get("always_show", True))

    # -- widgets ----------------------------------------------------------------------------
    def build(self):
        self.revealer = Gtk.Revealer()
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.revealer.set_transition_duration(200)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_bottom(10)
        corner = self.ctx.island.corner
        if corner.endswith("right"):
            box.set_margin_end(30)
        else:
            box.set_margin_start(30)
        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Search apps, files, commands…   ? for everything")
        self.entry.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, resolve_icon("search"))
        self.entry.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, resolve_icon("help"))
        self.entry.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, "Everything the bar can do  (?)")
        self.entry.set_icon_activatable(Gtk.EntryIconPosition.PRIMARY, False)
        self.entry.connect("icon-press", self._icon_pressed)
        self.entry.connect("changed", self._changed)
        self.entry.connect("activate", lambda _e: self._run_selected(alt=False))
        self.entry.connect("key-press-event", self._key)
        self.entry.connect("focus-in-event", self._focused)
        box.pack_start(self.entry, False, False, 0)

        self.help = Gtk.Revealer()
        self.help.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.help.set_transition_duration(180)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(HELP_HEIGHT)
        scroller.set_max_content_height(HELP_HEIGHT)
        scroller.get_style_context().add_class("cw-help")
        self.help_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        scroller.add(self.help_list)
        self.help.add(scroller)
        box.pack_start(self.help, False, False, 0)

        self.answer = label("", "cw-strong")
        self.answer.set_margin_start(12)
        self.answer.set_no_show_all(True)
        box.pack_start(self.answer, False, False, 0)
        self.results = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        box.pack_start(self.results, False, False, 0)
        self.revealer.add(box)
        self.revealer.set_reveal_child(self.always_show)
        return self.revealer

    def _built(self) -> bool:
        return hasattr(self, "entry")

    # -- island hooks -------------------------------------------------------------------
    def activate(self, first_char: str) -> bool:
        if not self._built():
            return False
        self.revealer.set_reveal_child(True)
        if self.config.get("apps", True):
            self._apps.ensure(self._refresh)
        self.entry.grab_focus()
        self.entry.set_text(first_char)
        self.entry.set_position(-1)
        return True

    def _opened(self) -> None:
        if self.config.get("apps", True):
            self._apps.ensure()
        if not self._built():
            return
        # The bar is visible but must not hold focus on its own: an empty, focused bar would keep
        # the panel open when the pointer leaves.
        def unfocus() -> bool:
            if self._built() and self.entry.has_focus() and not self.entry.get_text():
                self.ctx.island.set_focus(None)
                self._show_rows([])
            return False

        GLib.idle_add(unfocus)
        GLib.timeout_add(120, unfocus)

    def _escape(self) -> bool:
        if not self._built():
            return False
        if self.help.get_reveal_child():
            self._set_help(False)
            return True
        if self.entry.get_text() or self._matches:
            self.entry.set_text("")
            self._show_rows([])
            return True
        if self.entry.has_focus() or self.revealer.get_reveal_child() and not self.always_show:
            self.reset()
            self.ctx.island.set_focus(None)
            return True
        return False

    def reset(self) -> None:
        if self._built():
            self.entry.set_text("")
            self._set_help(False)
            self.revealer.set_reveal_child(self.always_show)
            self.answer.hide()
            self._show_rows([])
        self._history_pos = -1

    def _focused(self, *_args) -> bool:
        if not self.entry.get_text():
            GLib.idle_add(lambda: (self._show_recent() if self._built() and self.entry.has_focus()
                                   and not self.entry.get_text() else None) and False)
        return False

    def _icon_pressed(self, _entry, position, _event) -> None:
        if position == Gtk.EntryIconPosition.SECONDARY:
            self._set_help(not self.help.get_reveal_child())

    # -- help (?) ---------------------------------------------------------------------------
    def _set_help(self, show: bool) -> None:
        if not self._built():
            return
        if show:
            self._fill_help()
            self.revealer.set_reveal_child(True)
            self._show_rows([])
        self.help.set_reveal_child(show)

    def _fill_help(self) -> None:
        for child in self.help_list.get_children():
            self.help_list.remove(child)
        sections = [(heading, list(rows)) for heading, rows in GUIDE]
        search = [(f"{key} …", f"Search {cmdline.site_name(url)}") for key, url in sorted(self.engines.items())]
        search.append(("anything else", "Search the web" if self._search_url() else "Web search is off"))
        sections.insert(2, ("Search", search))
        groups: dict[str, list[tuple[str, str]]] = {}
        for command in self.ctx.host.commands():
            example = (command.usage or command.keywords[0]).split(" · ")[0]
            more = command.usage.split(" · ")[1:]
            text = command.title + (f"  ({', '.join(more)})" if more else "")
            if command.confirm:
                text += ", press Enter twice"
            groups.setdefault(command.group or "Other", []).append((example, text))
        for heading in sorted(groups):
            sections.append((heading, groups[heading]))
        for heading, rows in sections:
            self.help_list.pack_start(label(heading.upper(), "cw-help-head"), False, False, 0)
            for example, text in rows:
                self.help_list.pack_start(self._help_row(example, text), False, False, 0)
        self.help_list.pack_start(label("KEYS", "cw-help-head"), False, False, 0)
        for keys, text in KEYS:
            row = Gtk.Box(spacing=8)
            row.set_margin_start(9)
            row.set_margin_end(9)
            row.set_margin_top(2)
            row.set_margin_bottom(2)
            key = label(keys, "cw-key")
            key.set_valign(Gtk.Align.START)
            row.pack_start(key, False, False, 0)
            desc = label(text, "cw-dim cw-small")
            desc.set_line_wrap(True)
            row.pack_start(desc, True, True, 0)
            self.help_list.pack_start(row, False, False, 0)
        self.help_list.show_all()

    def _help_row(self, example: str, text: str) -> Gtk.Widget:
        button = Gtk.Button()
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_can_focus(False)
        button.get_style_context().add_class("cw-row")
        button.get_style_context().add_class("cw-help-row")
        row = Gtk.Box(spacing=10)
        example_label = label(example, "cw-help-example")
        example_label.set_width_chars(13)
        example_label.set_max_width_chars(16)
        example_label.set_ellipsize(Pango.EllipsizeMode.END)
        row.pack_start(example_label, False, False, 0)
        desc = label(text, "cw-dim cw-small")
        desc.set_line_wrap(True)
        desc.set_max_width_chars(30)
        row.pack_start(desc, True, True, 0)
        button.add(row)
        if example.endswith(" …") or example == "anything else":
            fill = example.removesuffix(" …") + " " if example.endswith(" …") else ""
        else:
            fill = example
        button.set_tooltip_text(f"Put “{fill.strip()}” in the bar" if fill else None)
        button.connect("clicked", lambda _b, f=fill: self._fill(f))
        return button

    def _fill(self, text: str) -> None:
        self._set_help(False)
        self.entry.grab_focus()
        self.entry.set_text(text)
        self.entry.set_position(-1)

    # -- matching -----------------------------------------------------------------------
    def _refresh(self) -> None:
        if self._built() and self.entry.get_text().strip():
            self._changed(self.entry)

    def _changed(self, entry) -> None:
        self._gen += 1
        self._disarm()
        text = entry.get_text()
        if not self._filling:
            self._history_pos = -1
        self.answer.hide()
        query = text.strip()
        if query and self.help.get_reveal_child() and query != "?":
            self.help.set_reveal_child(False)
        if not query:
            if entry.has_focus():
                self._show_recent()
            else:
                self._show_rows([])
            return
        if query == "?":
            self._set_help(True)
            return
        try:
            rows = self._match(text, query)
        except Exception:
            _log.exception("Matching %r failed", query)
            rows = []
        self._show_rows(rows)

    def _match(self, text: str, query: str) -> list[Result]:
        shell = cmdline.shell_line(text)
        if shell is not None:
            return self._shell_rows(*shell)
        if query.startswith(":") and len(query) > 1:
            return self._emoji_rows(query[1:])
        if cmdline.looks_like_path(query):
            return self._path_rows(query)

        results: list[Result] = []
        answer = self._answer(query)
        if answer is not None:
            results.append(answer)
        answered = answer is not None

        engine = cmdline.engine_query(query, self.engines)
        if engine is not None:
            key, url, words = engine
            results.append(Result(f"Search {cmdline.site_name(url)} for “{words}”", f"{key} …",
                                  lambda u=url: self._open(u), "web", True, key=f"engine:{key}"))

        url = cmdline.as_url(query)
        if url is not None:
            results.append(Result(f"Open {url.split('://', 1)[1]}", "Website", lambda u=url: self._open(u),
                                  "link", True, key=f"url:{url}"))

        word, _, arg = query.partition(" ")
        found = []
        for command in self.ctx.host.commands():
            if command.matches(word):
                exact = word.lower() in command.keywords
                found.append((command, exact))
        found.sort(key=lambda m: (not m[1], len(m[0].keywords[0])))
        any_exact = any(e for _c, e in found)
        for command, exact in found[:MAX_RESULTS]:
            results.extend(self._command_rows(command, arg, exact))

        if len(query) >= 2 and not answered:
            if self.config.get("windows", True):
                results += self._window_results(query)
            apps: list[AppEntry] = []
            if self.config.get("apps", True):
                room = max(MAX_RESULTS - len(results), 2 if not any_exact else 0)
                bonus = lambda e: self.history.bonus(f"app:{e.ref.get_id()}")  # noqa: E731
                apps = rank_apps(query, self._apps.entries, room, bonus=bonus)
                results += [Result(app.name, "App", lambda a=app: self._launch(a), app.ref.get_icon(), True,
                                   key=f"app:{app.ref.get_id()}") for app in apps]
            program = cmdline.first_word(query)
            app_programs = {(a.ref.get_executable() or "").rsplit("/", 1)[-1] for a in apps}
            if (self.config.get("path_programs", True) and not any_exact and program
                    and program not in app_programs and proc.which(program)):
                results.append(self._program_row(query))
            search = self._search_url()
            if search and not any_exact and engine is None and url is None:
                results.append(Result(f"Search the web for “{query}”", "Web",
                                      lambda q=query, u=search: self._open(cmdline.search_url(u, q)), "web", True,
                                      key="web"))
        return results

    def _search_url(self) -> str:
        return str(self.config.get("search_url", "https://duckduckgo.com/?q={}") or "")

    def _command_rows(self, command: Command, arg: str, exact: bool) -> list[Result]:
        key = f"cmd:{command.keywords[0]}"
        if exact and command.suggest is not None:
            try:
                suggestions = command.suggest(arg) or []
            except Exception:
                _log.exception("%s suggest failed", command.keywords[0])
                suggestions = []
            if suggestions:
                return [self._from_suggestion(s, key) for s in suggestions[:MAX_RESULTS]]
        title = command.title + (f"  {arg}" if arg and command.takes_arg else "")
        return [Result(title, command.usage, lambda c=command, a=arg: c.run(a), command.icon or None,
                       confirm=command.confirm, key=key)]

    @staticmethod
    def _from_suggestion(s: Suggestion, key: str) -> Result:
        return Result(s.title, s.detail, s.run, s.icon, s.closes, s.complete, key=key, confirm=s.confirm)

    # -- answers: maths, units, bases, currencies, times ----------------------------------
    def _answer(self, query: str) -> Result | None:
        def copy_row(text: str, copy: str, detail: str = "Enter copies", icon: str = "calc") -> Result:
            return Result(f"= {text}", detail, lambda c=copy: self._copy(c), icon, answer=True)

        base = calc.convert_base(query)
        if base is not None:
            return copy_row(*base)
        conversion = calc.convert(query)
        if conversion is not None:
            text = calc.format_conversion(conversion)
            return copy_row(text, text.rpartition(" ")[0].replace(" ", ""))
        currency = calc.parse_currency(query)
        if currency is not None:
            return self._currency_row(*currency)
        try:  # before maths, so 2026-12-25 is a date and not a subtraction
            when = clock.evaluate(query, dt.datetime.now().astimezone())
        except Exception:
            _log.debug("time answer failed for %r", query, exc_info=True)
            when = None
        if when is not None:
            return Result(when.text, "Enter copies", lambda c=when.copy: self._copy(c), "clock", answer=True)
        if calc.looks_like_math(query):
            try:
                value = calc.evaluate(query)
                text = calc.format_number(value)
                return copy_row(text, text.replace(" ", ""))
            except _CALC_ERRORS:
                pass
        return None

    def _currency_row(self, amount: float, src: str, dst: str) -> Result:
        if not self.rates.url:
            return Result("Currency conversion is off", "Settings → Command bar", lambda: None, "money")
        gen = self._gen
        self.rates.ensure(lambda: self._refresh() if gen == self._gen else None)
        value = calc.convert_currency(amount, src, dst, self.rates.rates) if self.rates.rates else None
        if value is None:
            busy = not self.rates.rates or src not in self.rates.rates and src != "EUR"
            return Result("Fetching exchange rates…" if busy else f"No rate for {src} → {dst}", "",
                          lambda: None, "money")
        text = calc.format_money(value, dst)
        detail = f"ECB {self.rates.date}" if self.rates.date else "Enter copies"
        return Result(f"= {text}", detail, lambda: self._copy(f"{value:.2f}"), "money", answer=True)

    # -- shell, programs, paths, files, emoji ---------------------------------------------
    def _shell_rows(self, kind: str, command: str) -> list[Result]:
        if not command:
            return [Result("Type a command: > ls ~ runs it here, >> htop in a terminal", "", lambda: None, "terminal")]
        background = Result(f"Run  {command}", "Shift+Enter: in a terminal", lambda c=command: self._run_background(c),
                            "terminal", alt=lambda c=command: self._run_terminal(c), alt_closes=True,
                            key=f"shell:{command}")
        terminal = Result(f"Run  {command}  in a terminal", "Shift+Enter: here", lambda c=command: self._run_terminal(c),
                          "terminal", True, alt=lambda c=command: self._run_background(c), alt_closes=False,
                          key=f"shell:{command}")
        return [background, terminal] if kind == "background" else [terminal, background]

    def _program_row(self, line: str) -> Result:
        return Result(f"Run  {line}  in a terminal", "Command · Shift+Enter: here",
                      lambda c=line: self._run_terminal(c), "terminal", True,
                      alt=lambda c=line: self._run_background(c), alt_closes=False, key=f"shell:{line}")

    def _run_background(self, command: str) -> str:
        try:
            launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
            launcher.set_cwd(str(Path.home()))
            process = launcher.spawnv(["sh", "-c", command])
        except GLib.Error as exc:
            return f"Could not run: {exc.message}"

        def finished(proc_, result) -> None:
            try:
                _ok, out, _err = proc_.communicate_utf8_finish(result)
            except GLib.Error as exc:
                out = exc.message
            status = proc_.get_exit_status() if proc_.get_if_exited() else -1
            lines = [ln for ln in (out or "").splitlines() if ln.strip()]
            last = lines[-1].strip()[:160] if lines else ""
            if status == 0:
                message = last or "Done"
            else:
                message = f"Exit {status}" + (f": {last}" if last else "")
            if self._built() and self.ctx.island.is_open and not self.entry.get_text():
                self._feedback(message, 4000)
            else:
                self.ctx.notify(f"{'Finished' if status == 0 else 'Failed'}: {command[:60]}", message,
                                "utilities-terminal-symbolic")

        process.communicate_utf8_async(None, None, finished)
        return f"Running {command[:60]}…"

    def _run_terminal(self, command: str) -> str:
        argv = cmdline.terminal_argv(command, str(self.config.get("terminal", "") or ""), proc.which)
        if argv is None:
            return "No terminal found (set one in Settings → Command bar)"
        try:
            subprocess.Popen(argv, cwd=str(Path.home()), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        except OSError as exc:
            return f"Could not start {argv[0]}: {exc}"
        return f"Opening {argv[0]}"

    def _path_rows(self, query: str) -> list[Result]:
        rows = []
        target = cmdline.expand(query)
        if target.exists() and not query.endswith("/") or target == Path.home() and query == "~":
            rows.append(self._file_row(target, f"Open {cmdline.tilde(target)}"))
        for item in cmdline.complete_path(query, MAX_RESULTS):
            if item.path == target:
                continue
            row = self._file_row(item.path, item.display)
            row.complete = item.display
            rows.append(row)
        if not rows:
            rows.append(Result("No such file or folder", "", lambda: None, "folder"))
        return rows[:MAX_RESULTS]

    def _file_row(self, path: Path, title: str) -> Result:
        is_dir = path.is_dir()
        detail = "Folder" if is_dir else "File · Shift+Enter: folder"
        icon: Any = "folder" if is_dir else "file"
        try:
            content_type, _uncertain = Gio.content_type_guess(str(path), None)
            if content_type and not is_dir:
                icon = Gio.content_type_get_symbolic_icon(content_type)
        except Exception:
            pass
        return Result(title, detail, lambda p=path: self._open(p.as_uri()), icon, True,
                      complete=cmdline.tilde(path) + ("/" if is_dir else ""),
                      alt=lambda p=path: self._open(p.parent.as_uri()), key=f"file:{path}")

    def _find_rows(self, arg: str) -> list[Suggestion]:
        query = arg.strip()
        if len(query) < 2:
            return [Suggestion("Type part of a file name", "", lambda: None, "file")]
        gen = self._gen

        def done(paths: list[str]) -> None:
            if gen != self._gen or not self._built():
                return
            rows = [self._file_row(Path(p), os.path.basename(p)) for p in paths]
            for row, p in zip(rows, paths):
                row.detail = cmdline.tilde(os.path.dirname(p))
                row.complete = None
            if not rows:
                rows = [Result(f"No files named like “{query}”", "", lambda: None, "file")]
            self._show_rows(rows)

        def start() -> bool:
            if gen == self._gen:  # still the same text after a short pause
                tasks.run_async(lambda: files.search(query, MAX_RESULTS), done)
            return False

        GLib.timeout_add(220, start)
        return [Suggestion(f"Searching for “{query}”…", "", lambda: None, "search")]

    def _emoji_rows(self, query: str) -> list[Result]:
        found = emoji.search(query, limit=MAX_RESULTS + 1)
        if not found:
            return [Result(f"No emoji or symbol called “{query}”", "", lambda: None, "emoji")]
        return [Result(f"{char}    {name}", "Copy", lambda c=char: self._copy(c), None, key="emoji")
                for char, name, _kw in found]

    # -- recents ---------------------------------------------------------------------------
    def _show_recent(self) -> None:
        rows = []
        for entry in self.history.recent(5):
            line, key, title = entry["line"], entry.get("key", ""), entry.get("title") or entry["line"]
            if key.startswith("app:"):
                app = self._apps.by_id(key[4:])
                if app is None:
                    continue
                rows.append(Result(app.name, "Recent", lambda a=app: self._launch(a), app.ref.get_icon(), True,
                                   key=key))
            else:
                rows.append(Result(line, "Recent", lambda ln=line, k=key: self._rerun(ln, k), "history",
                                   complete=line, key=""))
        self._show_rows(rows)

    def _rerun(self, line: str, key: str) -> str | None:
        """Run a recent line again when the same kind of row comes up first; otherwise just
        put it back in the bar."""
        self.entry.set_text(line)
        self.entry.set_position(-1)
        if self._matches and key and self._matches[0].key == key and not self._matches[0].confirm:
            self._run(0)
        return "__keep__"

    # -- rendering & keys -------------------------------------------------------------------
    def _show_rows(self, rows: list[Result]) -> None:
        self._matches = rows
        self._selected = 0
        self._render()

    def _render(self) -> None:
        if not self._built():
            return
        for child in self.results.get_children():
            self.results.remove(child)
        for index, result in enumerate(self._matches):
            button = Gtk.Button()
            button.set_relief(Gtk.ReliefStyle.NONE)
            button.set_can_focus(False)
            button.get_style_context().add_class("cw-row")
            row = Gtk.Box(spacing=8)
            if result.icon is not None:
                if isinstance(result.icon, str):
                    image = Gtk.Image.new_from_icon_name(resolve_icon(result.icon), Gtk.IconSize.MENU)
                else:
                    image = Gtk.Image.new_from_gicon(result.icon, Gtk.IconSize.MENU)
                image.set_pixel_size(16)
                row.pack_start(image, False, False, 0)
            armed = result is self._armed
            title = Gtk.Label(label=f"Press Enter again: {result.title}" if armed else result.title)
            title.set_xalign(0)
            title.set_ellipsize(Pango.EllipsizeMode.END)
            if result.answer:
                title.get_style_context().add_class("cw-answer")
                title.set_selectable(False)
            row.pack_start(title, True, True, 0)
            detail = "Enter to confirm" if result.confirm and not armed and index == self._selected else result.detail
            row.pack_end(label(detail, "cw-dim cw-small", xalign=1.0), False, False, 0)
            button.add(row)
            if result.answer or len(result.title) > 40:
                button.set_tooltip_text(result.title)
            button.connect("clicked", lambda _b, i=index: self._run(i))
            set_active(button, index == self._selected)
            if armed:
                button.get_style_context().add_class("cw-confirm")
            self.results.pack_start(button, False, False, 0)
        self.results.show_all()

    def _move(self, step: int) -> None:
        if self._matches:
            self._selected = (self._selected + step) % len(self._matches)
            self._disarm(render=False)
            self._render()

    def _key(self, _entry, event) -> bool:
        key = event.keyval
        shift = bool(event.state & Gdk.ModifierType.SHIFT_MASK)
        text = self.entry.get_text()
        if key == Gdk.KEY_Up and (not text or self._history_pos >= 0):
            return self._recall(+1)
        if key == Gdk.KEY_Down and self._history_pos >= 0 and not self._matches:
            return self._recall(-1)
        if key == Gdk.KEY_Down and self._matches:
            self._move(+1)
            return True
        if key == Gdk.KEY_Up and self._matches:
            self._move(-1)
            return True
        if key in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            if not self._matches:
                return True
            current = self._matches[self._selected]
            if key == Gdk.KEY_Tab and current.complete and current.complete != text:
                self.entry.set_text(current.complete)
                self.entry.set_position(-1)
            else:
                self._move(-1 if key == Gdk.KEY_ISO_Left_Tab or shift else +1)
            return True
        if key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and shift:
            self._run_selected(alt=True)
            return True
        if key == Gdk.KEY_BackSpace and not text and not self.always_show:
            self.reset()
            return True
        return False

    def _recall(self, step: int) -> bool:
        lines = self.history.lines()
        if not lines:
            return True
        position = self._history_pos + step
        if position < 0:
            self._history_pos = -1
            self.entry.set_text("")
            return True
        position = min(position, len(lines) - 1)
        self._history_pos = position
        self._filling = True
        try:
            self.entry.set_text(lines[position])
        finally:
            self._filling = False
        self.entry.set_position(-1)
        return True

    def _run_selected(self, alt: bool) -> None:
        if self._matches:
            self._run(self._selected, alt)

    def _disarm(self, render: bool = True) -> None:
        if self._armed is None:
            return
        self._armed = None
        if self._armed_id:
            GLib.source_remove(self._armed_id)
            self._armed_id = 0
        if render:
            self._render()

    def _run(self, index: int, alt: bool = False) -> None:
        if index >= len(self._matches):
            return
        result = self._matches[index]
        if result.confirm and self._armed is not result:
            self._disarm(render=False)
            self._selected = index
            self._armed = result

            def expire() -> bool:
                self._armed_id = 0
                self._disarm()
                return False

            self._armed_id = GLib.timeout_add(4000, expire)
            self._render()
            return
        self._disarm(render=False)
        action = result.alt if alt and result.alt is not None else result.run
        line = self.entry.get_text()
        try:
            feedback = action()
        except Exception as exc:
            _log.exception("Command bar row failed")
            feedback = f"Failed: {exc}"
        if feedback == "__keep__":
            return
        if result.key and line.strip():
            self.history.record(line, result.key, result.title)
            tasks.run_async(self.history.save)
        closes = result.closes
        if alt and result.alt is not None and result.alt_closes is not None:
            closes = result.alt_closes
        if closes:
            self.reset()
            self.ctx.island.close()
            return
        if not self.ctx.island.is_open:
            return
        self.entry.set_text("")
        self._show_rows([])
        self._feedback(feedback or result.title)

    # -- actions ----------------------------------------------------------------------------
    def _window_results(self, query: str) -> list[Result]:
        """Open windows matching what you typed: Enter switches to one."""
        windows = self.ctx.services.windows
        if not windows.available():
            return []

        def switch(window) -> str:
            self.ctx.island.close("switch", hand_back=False)  # first, so it won't take focus back
            windows.activate(window)
            return ""

        return [Result(window.title, "Switch to", lambda w=window: switch(w), group.icon or "app", True)
                for group, window in windows.find(query, 3)]

    def _launch(self, app: AppEntry) -> str:
        display = Gdk.Display.get_default()
        context = display.get_app_launch_context() if display else None
        app.ref.launch([], context)
        return f"Opening {app.name}"

    def _open(self, uri: str) -> str:
        from ..platform.desktop import open_uri

        open_uri(uri)
        return "Opening…"

    def _copy(self, text: str) -> str:
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)
        return f"Copied {text}"

    def commands(self) -> list[Command]:
        def settings(_a: str) -> None:
            self.ctx.actions.run("settings.toggle")

        def help_(_a: str) -> str:
            def show() -> bool:
                self.entry.set_text("")
                self._set_help(True)
                return False

            GLib.idle_add(show)
            return "__keep__"

        def emoji_rows(arg: str) -> list[Suggestion]:
            if not arg.strip():
                return [Suggestion("Type a name: heart, smile, arrow, degree…", "", lambda: None, "emoji")]
            return [Suggestion(f"{c}    {n}", "Copy", lambda c=c: self._copy(c)) for c, n, _k in
                    emoji.search(arg, limit=MAX_RESULTS)] or \
                [Suggestion(f"No emoji or symbol called “{arg.strip()}”", "", lambda: None, "emoji")]

        def forget(_a: str) -> str:
            self.history.clear()
            tasks.run_async(self.history.save)
            return "Forgot the command bar history"

        def find_first(arg: str) -> str:
            paths = files.search(arg.strip(), 1)
            return self._open(Path(paths[0]).as_uri()) if paths else "No match"

        group = "Command bar"
        return [
            Command(("settings", "preferences"), "Open settings", "settings", settings, group=group,
                    icon="settings"),
            Command(("help",), "Everything the bar can do", "help", help_, group=group, icon="help"),
            Command(("f", "find"), "Find files by name", "f invoice · find tax pdf", find_first, True,
                    suggest=self._find_rows, group="Open", icon="file"),
            Command(("emoji", "symbol"), "Emoji and symbols", "emoji heart · :heart", lambda a: None, True,
                    suggest=emoji_rows, group="Text", icon="emoji"),
            Command(("forget",), "Clear the command bar history", "forget", forget, group=group, icon="history"),
        ]

    def _feedback(self, text: str, ms: int = 1800) -> None:
        if not self._built():
            return
        self.answer.set_text(text)
        self.answer.show()
        if self._feedback_id:
            GLib.source_remove(self._feedback_id)

        def hide() -> bool:
            self._feedback_id = 0
            if not self.entry.get_text():
                self.answer.hide()
            return False

        self._feedback_id = GLib.timeout_add(ms, hide)
