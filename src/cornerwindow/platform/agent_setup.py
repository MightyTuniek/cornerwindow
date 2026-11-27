"""`cornerwindow agent setup`: wire coding agents' own hooks to the AI agents card.

Without hooks the card guesses (a log written in the last 90 seconds = working). With them each
agent says exactly when it works, waits for you and finishes, and Claude Code's status line hands
over your real plan limits. Every agent keeps its hooks in its own format, so each is an
`Integration` here: where its config lives, which of its events mean working / waiting / done,
and how to add or take out our entries without touching anything else in the file.

  Claude Code  ~/.claude/settings.json        hooks + status line (real 5-hour / weekly limit %)
  Codex        ~/.codex/config.toml           `notify` (when a turn is done)
  Gemini CLI   ~/.gemini/settings.json        hooks
  Droid        ~/.factory/settings.json       hooks
  Cursor       ~/.cursor/hooks.json           hooks
  Copilot CLI  ~/.copilot/hooks/cornerwindow.json   a hooks file of our own
  OpenCode     ~/.config/opencode/plugins/cornerwindow.js   a plugin of our own

Our entries are recognised by their command (`... cornerwindow agent ...`), so running setup
again replaces them instead of adding twice, and `--remove` finds them. A file we change gets a
one-time `.cornerwindow-bak` copy first. A file we can't parse is left alone.

Run with no arguments in a terminal for the wizard; flags make it a plain command (install.sh,
scripts): `--all --yes`, `--agent claude,codex`, `--remove`, `--dry-run`, `--list`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

WORKING, WAITING, DONE, IDLE = "working", "waiting", "done", "idle"


_OURS = re.compile(r"\bagent\s+(?:(?:working|waiting|done|idle)\b.*--agent|statusline\b)")


def is_ours(command) -> bool:
    """`<launcher> agent working --agent x` or `<launcher> agent statusline ...` (by shape, so a
    launcher installed under another name or path is still recognised)."""
    return isinstance(command, str) and _OURS.search(command) is not None


def command_path() -> str:
    """How hooks should call us: an absolute path, since agents started from a desktop launcher
    may not have ~/.local/bin on their PATH."""
    found = shutil.which("cornerwindow")
    local = Path.home() / ".local/bin/cornerwindow"
    path = found or (str(local) if local.exists() else "cornerwindow")
    return shlex.quote(path)


# -- files -----------------------------------------------------------------------------------
class Unreadable(Exception):
    pass


def read_json(path: Path) -> dict:
    try:
        text = path.read_text()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise Unreadable(str(exc)) from exc
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise Unreadable(f"not plain JSON ({exc.msg}, line {exc.lineno})") from exc
    if not isinstance(data, dict):
        raise Unreadable("not a JSON object")
    return data


def write_text(path: Path, text: str) -> None:
    """Atomic, after a one-time backup of what was there."""
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_name(path.name + ".cornerwindow-bak")
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)
    tmp = path.with_name(path.name + ".cornerwindow-tmp")
    tmp.write_text(text)
    tmp.replace(path)


def write_json(path: Path, data: dict) -> None:
    write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def merge_hook_groups(hooks: dict, events: dict[str, str], command, remove: bool = False) -> dict:
    """Claude-style hooks ({"Event": [{"matcher"?, "hooks": [{"type": "command", "command"}]}]}):
    take out our entries everywhere, then (unless removing) add one group per event."""
    out = {}
    for event, groups in (hooks or {}).items():
        if not isinstance(groups, list):
            out[event] = groups
            continue
        kept = []
        for group in groups:
            if isinstance(group, dict) and isinstance(group.get("hooks"), list):
                inner = [h for h in group["hooks"] if not (isinstance(h, dict) and is_ours(h.get("command")))]
                if not inner:
                    continue
                group = {**group, "hooks": inner}
            kept.append(group)
        if kept:
            out[event] = kept
    if not remove:
        for event, state in events.items():
            out.setdefault(event, []).append({"hooks": [{"type": "command", "command": command(state)}]})
    return out


def merge_flat_hooks(hooks: dict, events: dict[str, str], entry, remove: bool = False) -> dict:
    """Flat hook lists ({"event": [{"command": ...}]}, Cursor and Copilot)."""
    out = {}
    for event, items in (hooks or {}).items():
        if not isinstance(items, list):
            out[event] = items
            continue
        kept = [h for h in items if not (isinstance(h, dict) and any(is_ours(h.get(k)) for k in ("command", "bash")))]
        if kept:
            out[event] = kept
    if not remove:
        for event, state in events.items():
            out.setdefault(event, []).append(entry(state))
    return out


def has_hooks(hooks) -> bool:
    if not isinstance(hooks, dict):
        return False
    for items in hooks.values():
        for item in items if isinstance(items, list) else ():
            if not isinstance(item, dict):
                continue
            if any(is_ours(item.get(k)) for k in ("command", "bash")):
                return True
            if any(isinstance(h, dict) and is_ours(h.get("command")) for h in item.get("hooks") or ()):
                return True
    return False


# -- agents ----------------------------------------------------------------------------------
@dataclass
class Result:
    agent: str
    path: Path
    changed: bool
    message: str


class Integration:
    id = ""
    name = ""
    binaries: tuple[str, ...] = ()
    folder = ""  # relative to home; its existence means the agent is installed
    env = ""  # the variable the agent itself uses to move that folder
    adds = "hooks: working, waiting for you, done"

    def __init__(self, home: Path, command: str) -> None:
        self.home, self.cmd = home, command

    def base(self) -> Path:
        if self.env and os.environ.get(self.env):
            return Path(os.environ[self.env]).expanduser()
        return self.home / self.folder

    def found(self) -> bool:
        return self.base().is_dir() or any(shutil.which(b) for b in self.binaries)

    def path(self) -> Path:
        raise NotImplementedError

    def report(self, state: str) -> str:
        return f"{self.cmd} agent {state} --agent {self.id}"

    def installed(self) -> bool:
        raise NotImplementedError

    def apply(self, remove: bool = False, dry: bool = False, **options) -> Result:
        raise NotImplementedError

    def _done(self, changed: bool, remove: bool, dry: bool, extra: str = "") -> Result:
        verb = ("would remove" if remove else "would set up") if dry else ("removed" if remove else "set up")
        message = (f"{verb} {self.adds}" if changed else "nothing to remove" if remove else "already set up") + extra
        return Result(self.name, self.path(), changed, message)


class SettingsHooks(Integration):
    """Agents with Claude-style hooks inside a JSON settings file."""

    file = "settings.json"
    events: dict[str, str] = {}

    def path(self):
        return self.base() / self.file

    def installed(self):
        try:
            return has_hooks(read_json(self.path()).get("hooks"))
        except Unreadable:
            return False

    def apply(self, remove=False, dry=False, **options):
        data = read_json(self.path())
        before = json.dumps(data, sort_keys=True)
        hooks = merge_hook_groups(data.get("hooks") or {}, self.events, self.report, remove)
        if hooks:
            data["hooks"] = hooks
        else:
            data.pop("hooks", None)
        extra = self.extra(data, remove, **options)
        changed = json.dumps(data, sort_keys=True) != before
        if changed and not dry:
            write_json(self.path(), data)
        return self._done(changed, remove, dry, extra)

    def extra(self, data: dict, remove: bool, **options) -> str:
        return ""


class ClaudeCode(SettingsHooks):
    id, name, binaries, folder, env = "claude", "Claude Code", ("claude",), ".claude", "CLAUDE_CONFIG_DIR"
    events = {"UserPromptSubmit": WORKING, "PostToolUse": WORKING, "Notification": WAITING, "Stop": DONE,
              "SessionEnd": IDLE}

    def statusline(self) -> str:
        """'ours', 'other: <command>' or '' (none)."""
        try:
            line = read_json(self.path()).get("statusLine")
        except Unreadable:
            return ""
        command = line.get("command") if isinstance(line, dict) else None
        if not command:
            return ""
        return "ours" if is_ours(command) else f"other: {command}"

    def extra(self, data, remove, statusline: bool = True, keep_statusline: bool = True, **_options):
        line = data.get("statusLine") if isinstance(data.get("statusLine"), dict) else None
        command = (line or {}).get("command") or ""
        ours = is_ours(command)
        previous = _then(command) if ours else command
        if remove or not statusline:
            if ours and remove:
                if previous:
                    data["statusLine"] = {**line, "command": previous}
                else:
                    data.pop("statusLine")
                return " and the status line" + (" (yours is back)" if previous else "")
            return ""
        if previous and not keep_statusline:
            previous = ""  # --replace-statusline: ours instead of theirs
        wanted = f"{self.cmd} agent statusline" + (f" --then {shlex.quote(previous)}" if previous else "")
        data["statusLine"] = {**(line or {}), "type": "command", "command": wanted}
        return " + real limit % in the status line" + (" (yours still shows)" if previous else "")


def _then(command: str) -> str:
    """The status line we wrapped: what follows `--then`."""
    _head, found, rest = command.partition(" --then ")
    if not found:
        return ""
    try:
        parts = shlex.split(rest)
    except ValueError:
        return rest
    return parts[0] if len(parts) == 1 else rest


class Gemini(SettingsHooks):
    id, name, binaries, folder = "gemini", "Gemini CLI", ("gemini",), ".gemini"
    events = {"BeforeAgent": WORKING, "Notification": WAITING, "AfterAgent": DONE}


class Droid(SettingsHooks):
    id, name, binaries, folder = "droid", "Droid", ("droid",), ".factory"
    events = {"UserPromptSubmit": WORKING, "PostToolUse": WORKING, "Notification": WAITING, "Stop": DONE,
              "SessionEnd": IDLE}


class Cursor(Integration):
    id, name, binaries, folder = "cursor", "Cursor", ("cursor-agent",), ".cursor"
    events = {"beforeSubmitPrompt": WORKING, "stop": DONE}
    adds = "hooks: working, done"

    def path(self):
        return self.base() / "hooks.json"

    def installed(self):
        try:
            return has_hooks(read_json(self.path()).get("hooks"))
        except Unreadable:
            return False

    def apply(self, remove=False, dry=False, **_options):
        data = read_json(self.path())
        if remove and not has_hooks(data.get("hooks")):
            return self._done(False, remove, dry)
        before = json.dumps(data, sort_keys=True)
        data.setdefault("version", 1)
        data["hooks"] = merge_flat_hooks(data.get("hooks") or {}, self.events,
                                         lambda s: {"command": self.report(s)}, remove)
        changed = json.dumps(data, sort_keys=True) != before
        if changed and not dry:
            write_json(self.path(), data)
        return self._done(changed, remove, dry)


class OwnFile(Integration):
    """Agents that read every file in a folder: we keep a file of our own there."""

    def text(self) -> str:
        raise NotImplementedError

    def installed(self):
        return self.path().is_file()

    def apply(self, remove=False, dry=False, **_options):
        path = self.path()
        if remove:
            changed = path.is_file()
            if changed and not dry:
                path.unlink()
            return self._done(changed, remove, dry)
        text = self.text()
        changed = not path.is_file() or path.read_text() != text
        if changed and not dry:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        return self._done(changed, remove, dry)


class Copilot(OwnFile):
    id, name, binaries, folder, env = "copilot", "Copilot CLI", ("copilot",), ".copilot", "COPILOT_HOME"
    events = {"userPromptSubmitted": WORKING, "postToolUse": WORKING, "notification": WAITING,
              "agentStop": DONE, "sessionEnd": IDLE}

    def path(self):
        return self.base() / "hooks" / "cornerwindow.json"

    def text(self):
        hooks = {e: [{"type": "command", "bash": self.report(s), "timeoutSec": 5}] for e, s in self.events.items()}
        return json.dumps({"version": 1, "hooks": hooks}, indent=2) + "\n"


class OpenCode(OwnFile):
    id, name, binaries = "opencode", "OpenCode", ("opencode",)
    adds = "a plugin: working, waiting for you, done"

    def base(self):
        return Path(os.environ.get("XDG_CONFIG_HOME") or self.home / ".config") / "opencode"

    def path(self):
        return self.base() / "plugins" / "cornerwindow.js"

    def text(self):
        cmd = json.dumps(shlex.split(self.cmd)[0] if self.cmd else "cornerwindow")
        return f"""// CornerWindow: tells the AI agents card when OpenCode works, waits for you or is done.
// Made by `cornerwindow agent setup`; `cornerwindow agent setup --remove` deletes it.
export const CornerWindow = async ({{ $, directory }}) => {{
  const tell = (state) =>
    $`${{{cmd}}} agent ${{state}} --agent opencode --project ${{directory}}`.quiet().nothrow()
  return {{
    event: async ({{ event }}) => {{
      const type = event.type
      if (type === "session.status" && event.properties?.status?.type === "busy") await tell("working")
      else if (type === "permission.asked") await tell("waiting")
      else if (type === "permission.replied") await tell("working")
      else if (type === "session.idle") await tell("done")
    }},
  }}
}}
"""


class Codex(Integration):
    id, name, binaries, folder, env = "codex", "Codex", ("codex",), ".codex", "CODEX_HOME"
    adds = "notify: done"

    def path(self):
        return self.base() / "config.toml"

    def notify(self) -> str | None:
        """The current top-level `notify` line, if any."""
        try:
            lines = self.path().read_text().splitlines()
        except OSError:
            return None
        for line in lines:
            if line.lstrip().startswith("["):
                return None
            if re.match(r"\s*notify\s*=", line):
                return line
        return None

    @staticmethod
    def ours(line: str | None) -> bool:
        return bool(line) and ('"agent", "done", "--agent"' in line or "cornerwindow" in line)

    def installed(self):
        return self.ours(self.notify())

    def apply(self, remove=False, dry=False, **_options):
        path = self.path()
        try:
            text = path.read_text()
        except FileNotFoundError:
            text = ""
        current = self.notify()
        program = shlex.split(self.cmd)[0] if self.cmd else "cornerwindow"
        wanted = "notify = " + json.dumps([program, "agent", "done", "--agent", "codex"])
        if current and not self.ours(current):
            return Result(self.name, path, False, "skipped: you already have a notify program ("
                                                  f"{current.split('=', 1)[1].strip()})")
        lines = text.splitlines()
        if current:
            index = lines.index(current)
            if remove:
                del lines[index]
            elif current != wanted:
                lines[index] = wanted
        elif not remove:
            # top-level keys must come before the first [table]
            first = next((i for i, line in enumerate(lines) if line.lstrip().startswith("[")), len(lines))
            lines[first:first] = [wanted, ""] if first < len(lines) else [wanted]
        new = "\n".join(lines) + ("\n" if lines else "")
        changed = new != text
        if changed and not dry:
            write_text(path, new)
        return self._done(changed, remove, dry)


INTEGRATIONS = [ClaudeCode, Codex, Gemini, OpenCode, Cursor, Copilot, Droid]


def integrations(home: Path | None = None, command: str | None = None) -> list[Integration]:
    home = home or Path.home()
    command = command or command_path()
    return [cls(home, command) for cls in INTEGRATIONS]


# -- the card --------------------------------------------------------------------------------
def card_shown() -> bool:
    from .. import config

    cfg = config.load()
    layout = [m for col in cfg.get("modules.columns", []) for m in col] + list(cfg.get("modules.top", []))
    return "agents" in layout and "agents" not in cfg.get("modules.hidden", [])


def show_card() -> str:
    """Put the AI agents card at the top of the first column (and un-hide it)."""
    from .. import config
    from ..config_edit import UserConfigWriter
    from ..core import paths

    cfg = config.load()
    writer = UserConfigWriter(cfg.user_file or paths.user_config_file())
    base = cfg.base.get("modules", {})
    hidden = [m for m in cfg.get("modules.hidden", []) if m != "agents"]
    columns = [list(c) for c in cfg.get("modules.columns", [])] or [[]]
    values = {"hidden": hidden}
    if not any("agents" in c for c in columns):
        columns[0].insert(0, "agents")
        values["columns"] = columns
    writer.set_many("modules", values, {k: base.get(k) for k in values})
    return "the AI agents card is in the panel (top of the first column; move it in Settings → Features)"


# -- the command -----------------------------------------------------------------------------
BOLD, DIM, GREEN, YELLOW, CYAN, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[1;36m", "\033[0m"


def _color(on: bool):
    def paint(code: str, text: str) -> str:
        return f"{code}{text}{RESET}" if on else text
    return paint


def _home_path(path: Path) -> str:
    home = str(Path.home())
    text = str(path)
    return "~" + text[len(home):] if text.startswith(home + "/") else text


def _ask(question: str, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    while True:
        try:
            answer = input(f"{question} {hint} ").strip().lower()
        except EOFError:
            print()
            return default
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cornerwindow agent setup",
        description="Connect coding agents' hooks to CornerWindow's AI agents card, so it knows exactly "
                    "when each agent works, waits for you or is done (and Claude Code's real limit %). "
                    "With no options in a terminal: a wizard.")
    p.add_argument("--all", action="store_true", help="every agent found on this computer")
    p.add_argument("--agent", "-a", action="append", default=[], metavar="NAME[,NAME]",
                   help="only these: " + ", ".join(cls.id for cls in INTEGRATIONS))
    p.add_argument("--yes", "-y", action="store_true", help="don't ask (implies --all without --agent)")
    p.add_argument("--remove", action="store_true", help="take CornerWindow's hooks out again")
    p.add_argument("--no-statusline", action="store_true", help="leave Claude Code's status line alone")
    p.add_argument("--replace-statusline", action="store_true",
                   help="drop your own Claude status line instead of running it after ours")
    p.add_argument("--card", dest="card", action="store_true", default=None, help="switch the AI agents card on")
    p.add_argument("--no-card", dest="card", action="store_false", help="don't switch the card on")
    p.add_argument("--dry-run", "-n", action="store_true", help="show what would change, change nothing")
    p.add_argument("--list", "-l", action="store_true", help="show which agents are found and set up")
    return p


def main(argv: list[str], home: Path | None = None, command: str | None = None,
         interactive: bool | None = None) -> int:
    args = parser().parse_args(argv)
    paint = _color(sys.stdout.isatty() and not os.environ.get("NO_COLOR"))
    every = integrations(home, command)
    by_id = {i.id: i for i in every}
    wanted = [n.strip().lower() for chunk in args.agent for n in chunk.split(",") if n.strip()]
    unknown = [n for n in wanted if n not in by_id]
    if unknown:
        print(f"unknown agent(s): {', '.join(unknown)} (known: {', '.join(by_id)})", file=sys.stderr)
        return 2
    found = [i for i in every if i.found()]
    flags = args.all or args.yes or wanted or args.remove or args.dry_run
    if interactive is None:
        interactive = sys.stdin.isatty() and sys.stdout.isatty() and not flags and not args.list

    if args.list or (not flags and not interactive):
        _status(every, paint)
        if not args.list:
            print(f"\nRun {paint(BOLD, 'cornerwindow agent setup')} in a terminal for the wizard, or "
                  f"{paint(BOLD, 'cornerwindow agent setup --all --yes')} to set up every agent found.")
        return 0

    options = {"statusline": not args.no_statusline, "keep_statusline": not args.replace_statusline}
    card = args.card
    if interactive:
        chosen, options, card = _wizard(every, found, options, card, paint)
        if chosen is None:
            return 0
    else:
        chosen = [by_id[n] for n in wanted] if wanted else (
            [i for i in every if i.installed() or i.found()] if args.remove else found)
        if card is None:
            card = False

    if not chosen:
        print("No coding agents found to set up." if not args.remove else "Nothing to remove.")
    status = 0
    for integration in chosen:
        try:
            result = integration.apply(remove=args.remove, dry=args.dry_run, **options)
        except Unreadable as exc:
            print(f"  {paint(YELLOW, '•')} {integration.name}: left alone, can't read "
                  f"{_home_path(integration.path())}: {exc}")
            status = 1
            continue
        except OSError as exc:
            print(f"  {paint(YELLOW, '•')} {integration.name}: {exc}")
            status = 1
            continue
        mark = paint(GREEN, "✔") if result.changed or "already" in result.message else paint(YELLOW, "•")
        print(f"  {mark} {result.agent}: {result.message}  {paint(DIM, _home_path(result.path))}")
    if card and not args.remove:
        try:
            if card_shown():
                print(f"  {paint(GREEN, '✔')} the AI agents card is already in the panel")
            elif args.dry_run:
                print(f"  {paint(GREEN, '✔')} would put the AI agents card in the panel")
            else:
                print(f"  {paint(GREEN, '✔')} {show_card()}")
        except Exception as exc:  # the config is the user's: say so rather than crash
            print(f"  {paint(YELLOW, '•')} couldn't switch the card on ({exc}); do it in Settings → Features")
            status = 1
    if chosen and not args.dry_run:
        print(paint(DIM, "Agents read their hooks when they start: restart sessions that are open now."))
    return status


def _status(every: list[Integration], paint) -> None:
    print(paint(BOLD, "Coding agents"))
    for i in every:
        if i.installed():
            state = paint(GREEN, "set up      ")
        elif i.found():
            state = paint(YELLOW, "not set up  ")
        else:
            state = paint(DIM, "not found   ")
        print(f"  {state}{i.name:<13}{paint(DIM, _home_path(i.path()))}")


def _wizard(every, found, options, card, paint):
    print(f"\n{paint(CYAN, '›')} {paint(BOLD, 'Coding agents on the AI agents card')}")
    print("  Agents' own hooks tell the card exactly when each one works, waits for you or is done,\n"
          "  and Claude Code's status line gives it your real 5-hour and weekly limit %.\n")
    if not found:
        print("  No coding agents found (Claude Code, Codex, Gemini CLI, OpenCode, Cursor, Copilot CLI, Droid).\n"
              "  Install one and run `cornerwindow agent setup` again.")
        return None, options, card
    for i in found:
        done = paint(GREEN, " (set up)") if i.installed() else ""
        print(f"  • {paint(BOLD, i.name)}{done}  {paint(DIM, i.adds + ' · ' + _home_path(i.path()))}")
    missing = [i.name for i in every if i not in found]
    if missing:
        print(paint(DIM, f"  Not found: {', '.join(missing)}"))
    print()
    if _ask(f"Set up {'all ' + str(len(found)) if len(found) > 1 else found[0].name}?"):
        chosen = list(found)
    elif len(found) > 1:
        chosen = [i for i in found if _ask(f"  {i.name}?", default=False)]
    else:
        chosen = []
    claude = next((i for i in chosen if isinstance(i, ClaudeCode)), None)
    if claude is not None:
        line = claude.statusline()
        if line.startswith("other: "):
            print(f"\n  Claude Code already has a status line: {paint(DIM, line[7:])}")
            options["statusline"] = _ask("  Run CornerWindow's in front of it (yours still shows) for your real limit %?")
            options["keep_statusline"] = True
        elif line != "ours":
            options["statusline"] = _ask("\n  Let Claude Code's status line send your real limit % to the card?\n"
                                         "  (It shows model · folder · 5h limit %.)")
    if card is None:
        try:
            card = False if card_shown() else _ask("\n  Show the AI agents card in the panel? (it's off by default)")
        except Exception:
            card = False
    print()
    if not chosen and not card:
        print("  Nothing changed. Run `cornerwindow agent setup` any time.")
        return None, options, card
    return chosen, options, card
