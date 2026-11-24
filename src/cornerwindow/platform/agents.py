"""Coding-agent usage from their own local logs (nothing is sent anywhere, no sign-in).

Every agent is a `Source` in `SOURCES`: where its files live (with the override variable the tool
itself honours), how to read them (the formats are in agent_formats.py), and how to name the
project. Readers are incremental: a JSONL file is read from where the last read stopped, a JSON
file or database only when it changed, so a big log costs little after the first look. Agents
that aren't installed cost one `is_dir()`.

  Claude Code   ~/.claude/projects/*/*.jsonl (+ */subagents)   tokens, the 5-hour window
  Codex         ~/.codex/{sessions,archived_sessions}           tokens, plan limits (% in the log)
  OpenCode      ~/.local/share/opencode/opencode.db             tokens, cost (also OpenWork)
  Kilo          ~/.local/share/kilo/kilo.db                     tokens, cost
  Gemini CLI    ~/.gemini/tmp/*/chats/*.json(l)                 tokens
  Qwen Code     ~/.qwen/projects/*/chats/*.jsonl                tokens
  Antigravity   ~/.gemini/antigravity*/conversations/*.db       tokens
  Copilot CLI   ~/.copilot/session-state/*/events.jsonl         tokens
  Amp           ~/.local/share/amp/threads/**/*.json            tokens
  Droid         ~/.factory/sessions/**/*.settings.json          tokens (session totals)
  pi, Oh My Pi  ~/.pi/agent/sessions, ~/.omp/agent/sessions     tokens, cost
  OpenClaw      ~/.openclaw/agents/*/{sessions,agent}           tokens, cost
  Grok Build    ~/.grok/sessions/*/*/updates.jsonl              tokens, cost
  Kimi Code     ~/.kimi{,-code}/sessions/**/wire.jsonl          tokens
  Goose         ~/.local/share/goose/sessions/sessions.db       tokens (session totals)
  Hermes        ~/.hermes/state.db                              tokens, cost (session totals)
  Cline         ~/.cline, VS Code globalStorage                 tokens, cost
  Codebuff      ~/.config/manicode/projects/*/chats/*           tokens
  ZCode         ~/.zcode/cli/db/db.sqlite                       tokens
  DeepSeek H.   ~/.dsh/sessions/*/*/session*.jsonl              tokens

Agents that keep no usable usage locally (Cursor's CLI, Aider, Kiro, Auggie, Crush, ...) are found
by their running process instead (`ProcessSource`): "open in <project>", and "working" while the
process uses the CPU.

Which agent is *working* comes from, best first: states the agents report themselves
(`cornerwindow agent working|waiting|done`, from their hooks, see `AgentReports`), herdr's live
agent list, and failing both a log written in the last ACTIVE_SECONDS (or a busy process).

Claude's plan limits are counted in 5-hour windows that start with your first message after a
break (rounded down to the hour, the way usage tools such as ccusage count them); Codex writes
its own limit percentages into the log.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import unquote

from . import agent_formats as fmt
from .agent_formats import Limit, Usage, parse_claude, parse_codex  # noqa: F401  (the public API)

WINDOW = dt.timedelta(hours=5)
ACTIVE_SECONDS = 90  # a log written to this recently = the agent is working
_time = fmt.to_time


@dataclass
class AgentSummary:
    name: str
    today_tokens: int = 0
    today_messages: int = 0
    today_cost: float = 0.0
    window_tokens: int = 0
    window_start: dt.datetime | None = None
    window_end: dt.datetime | None = None
    models: list[str] = field(default_factory=list)
    working: list[str] = field(default_factory=list)  # projects with a session writing right now
    waiting: list[str] = field(default_factory=list)  # projects where the agent waits for you
    done: list[str] = field(default_factory=list)  # projects that finished in the last minutes
    running: list[str] = field(default_factory=list)  # projects with the agent open but idle (process only)
    limits: list[Limit] = field(default_factory=list)
    last_seen: dt.datetime | None = None
    id: str = ""


def current_window(times: list[dt.datetime], now: dt.datetime) -> tuple[dt.datetime, dt.datetime] | None:
    """The 5-hour window `now` falls in, or None when the last one has ended. A window starts at the
    first message after the previous window ended, rounded down to the hour."""
    start = end = None
    for at in sorted(times):
        if end is None or at >= end:
            start = at.replace(minute=0, second=0, microsecond=0)
            end = start + WINDOW
    if start is None or now >= end:
        return None
    return start, end


def merge(usages: list[Usage], policy: str = "first") -> list[Usage]:
    """One row per call: rows with the same key are one call. `policy` says which copy counts:
    the first seen, the last (a message rewritten in place) or the one with most tokens (a
    response streamed in pieces)."""
    best: dict = {}
    loose = []
    for u in usages:
        if u.key is None:
            loose.append(u)
            continue
        old = best.get(u.key)
        if old is None or policy == "last" or (policy == "max" and u.tokens > old.tokens):
            best[u.key] = u
    return list(best.values()) + loose


def _find_key(record, key: str, depth: int = 3):
    if not isinstance(record, dict) or depth < 0:
        return None
    if key in record:
        return record[key]
    for value in record.values():
        if isinstance(value, dict):
            found = _find_key(value, key, depth - 1)
            if found is not None:
                return found
    return None


def _cwd_in(lines) -> str:
    """The working folder a session log mentions (Claude, Codex, Qwen, pi and others write `cwd`)."""
    for line in lines:
        if '"cwd"' not in line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        cwd = _find_key(record, "cwd", 3)
        if isinstance(cwd, str) and cwd:
            return cwd
    return ""


def _project_name(folder: str) -> str:
    """'-home-user-cornerwindow' -> 'cornerwindow', '/home/user/app' -> 'app', a hash -> ''."""
    folder = str(folder or "")
    if "/" in folder or "\\" in folder:
        name = re.split(r"[\\/]", folder.rstrip("/\\"))[-1]
    else:
        name = folder.strip("-").rsplit("-", 1)[-1]
    if re.fullmatch(r"[0-9a-f]{16,}|[0-9a-f-]{36}|\d+", name or ""):
        return ""
    return name


# -- reading the files ---------------------------------------------------------------------
class _Reader:
    """Remembers, per file, how far it has read and what it found."""

    def __init__(self) -> None:
        self.files: dict[Path, dict] = {}

    def _entry(self, path: Path) -> dict:
        return self.files.setdefault(path, {"offset": 0, "items": [], "limits": [], "project": "",
                                            "stamp": None, "total": None, "state": {}})

    def read(self, path: Path, parse) -> dict:
        """A log only ever appended to: read the new complete lines."""
        entry = self._entry(path)
        try:
            size = path.stat().st_size
        except OSError:
            return entry
        if size < entry["offset"]:  # rewritten
            entry.update(offset=0, items=[], state={}, limits=[])
        if size > entry["offset"]:
            try:
                with open(path, "rb") as handle:
                    handle.seek(entry["offset"])
                    data = handle.read()
            except OSError:
                return entry
            cut = data.rfind(b"\n") + 1  # keep a half-written last line for next time
            lines = data[:cut].decode("utf-8", "replace").splitlines()
            entry["offset"] += cut
            if not entry["project"]:
                entry["project"] = _cwd_in(lines)
            parse(entry, lines)
        return entry

    def read_whole(self, path: Path, parse, totals: bool = False) -> dict:
        """A JSON file rewritten as a whole: parse it again when it changed. With `totals`, the file
        holds a running total, and what it grew by since the last look counts at the time it grew."""
        entry = self._entry(path)
        try:
            stat = path.stat()
        except OSError:
            return entry
        stamp = (stat.st_size, stat.st_mtime_ns)
        if stamp == entry["stamp"]:
            return entry
        entry["stamp"] = stamp
        try:
            data = json.loads(path.read_text("utf-8", "replace"))
        except (OSError, ValueError):
            return entry
        mtime = _time(stat.st_mtime)
        if not totals:
            entry["items"] = parse(data, mtime) or []
            return entry
        total = parse(data, mtime)
        if total is not None:
            entry["items"] += _growth(entry["total"], total, mtime)
            entry["total"] = total
        return entry


    def read_compressed(self, path: Path, parse) -> dict:
        """A zstd-compressed log: decode and parse it again whenever it changed."""
        entry = self._entry(path)
        try:
            stat = path.stat()
        except OSError:
            return entry
        stamp = (stat.st_size, stat.st_mtime_ns)
        if stamp == entry["stamp"]:
            return entry
        entry["stamp"] = stamp
        data = unzstd(path)
        if data is None:
            return entry
        cut = data.rfind(b"\n") + 1  # a torn last frame leaves a partial line
        lines = data[:cut].decode("utf-8", "replace").splitlines()
        entry.update(items=[], state={}, limits=[], project=entry["project"] or _cwd_in(lines))
        parse(entry, lines)
        return entry


def unzstd(path: Path) -> bytes | None:
    """The decompressed bytes of a .zst file, with whatever decoder this machine has: Python
    3.14's compression.zstd, the zstandard module, or the zstd command. None without one."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    try:
        from compression import zstd  # Python 3.14+

        return zstd.decompress(raw)
    except ImportError:
        pass
    except Exception:  # a torn last frame: fall through to a decoder that keeps the rest
        pass
    try:
        import zstandard

        return zstandard.ZstdDecompressor().decompressobj().decompress(raw)
    except ImportError:
        pass
    except Exception:
        return None
    if shutil.which("zstd"):
        try:
            done = subprocess.run(["zstd", "-dcq", "--", str(path)], capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout  # with a torn last frame it still holds everything before it
    return None


def _growth(before: Usage | None, now: Usage, at: dt.datetime) -> list[Usage]:
    """What a running total added since `before` (all of it the first time), counted at `at`."""
    def copy(u: Usage, **changes) -> Usage:
        values = dict(input=u.input, output=u.output, cache_read=u.cache_read, cache_write=u.cache_write,
                      extra=u.extra, cost=u.cost)
        values.update(changes)
        return Usage(at, model=u.model, **values)

    if before is None:
        return [] if now.empty else [copy(now)]
    grew = copy(now, input=now.input - before.input, output=now.output - before.output,
                cache_read=now.cache_read - before.cache_read, cache_write=now.cache_write - before.cache_write,
                extra=now.extra - before.extra, cost=max(0.0, now.cost - before.cost))
    if min(grew.input, grew.output, grew.cache_read, grew.cache_write, grew.extra) < 0:
        return [copy(now)]  # a new session in the same place: count it from zero
    return [] if grew.empty else [grew]


def _open_db(path: Path) -> sqlite3.Connection | None:
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0.2)
        db.row_factory = sqlite3.Row
        return db
    except sqlite3.Error:
        return None


def _db_stamp(path: Path):
    stamps = []
    for p in (path, Path(f"{path}-wal")):
        try:
            s = p.stat()
            stamps.append((s.st_size, s.st_mtime_ns))
        except OSError:
            stamps.append(None)
    return tuple(stamps)


def _db_mtime(path: Path) -> float:
    times = []
    for p in (path, Path(f"{path}-wal")):
        try:
            times.append(p.stat().st_mtime)
        except OSError:
            pass
    return max(times, default=0.0)


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}
    except sqlite3.Error:
        return set()


# -- the sources ---------------------------------------------------------------------------
class Source:
    """One agent. Subclasses say where its files are (`roots`, relative to home unless absolute;
    `env` overrides them, comma-separated) and implement `collect`."""

    id = ""
    name = ""
    aliases: tuple[str, ...] = ()  # how hooks and herdr may call it
    env = ""
    roots: tuple[str, ...] = ()
    window = False  # Claude-style 5-hour windows, inferred from the messages
    policy = "first"  # which copy of a call counts, see merge()

    def __init__(self, home: Path) -> None:
        self.home = home
        self.reader = _Reader()
        self.limits: list[Limit] = []

    def dirs(self) -> list[Path]:
        if self.env and os.environ.get(self.env, "").strip():
            return [Path(p.strip()).expanduser() for p in os.environ[self.env].split(",") if p.strip()]
        data = Path(os.environ.get("XDG_DATA_HOME") or self.home / ".local/share")
        config = Path(os.environ.get("XDG_CONFIG_HOME") or self.home / ".config")
        out = []
        for root in self.roots:
            root = root.replace("$DATA", str(data)).replace("$CONFIG", str(config))
            out.append(Path(root) if root.startswith("/") else self.home / root)
        return out

    def available(self) -> bool:
        return any(d.is_dir() for d in self.dirs())

    def names(self) -> set[str]:
        return {_norm(n) for n in (self.id, self.name, *self.aliases)}

    def collect(self, since: float, now: float) -> tuple[list[Usage], list[str]]:
        """(usages since `since`, one per call; projects with a session active right now)."""
        raise NotImplementedError


class FileSource(Source):
    globs: tuple[str, ...] = ()
    whole = False  # files rewritten as a whole (JSON) instead of appended to (JSONL)
    totals = False  # ...that hold a session's running total
    project_level: int | None = 1  # which parent folder names the project (None = no name)

    def parse(self, entry: dict, lines: list[str]) -> None:
        raise NotImplementedError

    def parse_whole(self, data, mtime):
        raise NotImplementedError

    def is_whole(self, path: Path) -> bool:
        return self.whole

    def items(self, entry: dict) -> list[Usage]:
        return entry["items"]

    def files(self, root: Path) -> list[Path]:
        out = []
        for pattern in self.globs:
            try:
                out += root.glob(pattern)
            except OSError:
                continue
        return out

    def project(self, path: Path, entry: dict) -> str:
        if entry.get("project"):
            return _project_name(entry["project"])
        if self.project_level is None:
            return ""
        return _project_name(path.parents[self.project_level - 1].name)

    def collect(self, since: float, now: float) -> tuple[list[Usage], list[str]]:
        usages, working, newest = [], [], 0.0
        for root in self.dirs():
            if not root.is_dir():
                continue
            for path in self.files(root):
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if mtime < since:
                    continue
                if path.suffix in (".zst", ".zstd"):
                    entry = self.reader.read_compressed(path, self.parse)
                elif self.is_whole(path):
                    entry = self.reader.read_whole(path, self.parse_whole, self.totals)
                else:
                    entry = self.reader.read(path, self.parse)
                usages += self.items(entry)
                if entry.get("limits") and mtime > newest:
                    newest, self.limits = mtime, entry["limits"]
                if now - mtime < ACTIVE_SECONDS:
                    working.append(self.project(path, entry) or "a session")
        return [u for u in merge(usages, self.policy) if u.at.timestamp() >= since], working


class Claude(FileSource):
    id, name, aliases = "claude", "Claude Code", ("claude-code", "claudecode", "cc")
    env, roots = "CLAUDE_CONFIG_DIR", (".claude", "$CONFIG/claude")
    globs = ("projects/*/*.jsonl", "projects/*/*/subagents/*.jsonl")  # subagents write their own logs
    window, policy = True, "max"

    def parse(self, entry, lines):
        entry["items"] += parse_claude(lines)

    def project(self, path, entry):
        if entry.get("project"):
            return _project_name(entry["project"])
        folder = path.parents[2] if path.parent.name == "subagents" else path.parent
        return _project_name(folder.name)


class Codex(FileSource):
    id, name, aliases = "codex", "Codex", ("codex-cli", "openai-codex")
    env, roots = "CODEX_HOME", (".codex",)
    globs = ("sessions/*/*/*/*.jsonl", "sessions/*/*/*/*.jsonl.zst", "archived_sessions/*.jsonl",
             "archived_sessions/*.jsonl.zst")

    def files(self, root):  # Codex restores the plain rollout before deleting the compressed copy
        found = super().files(root)
        plain = {p for p in found if p.suffix == ".jsonl"}
        return [p for p in found if p.suffix == ".jsonl" or p.with_suffix("") not in plain]

    def parse(self, entry, lines):
        usages, limits = parse_codex(lines, entry["state"])
        entry["items"] += usages
        if limits:
            entry["limits"] = limits

    def items(self, entry):  # a file with per-response records: its per-turn counts repeat them
        if any(u.kind == "record" for u in entry["items"]):
            return [u for u in entry["items"] if u.kind == "record"]
        return entry["items"]


class Gemini(FileSource):
    id, name, aliases = "gemini", "Gemini CLI", ("gemini-cli",)
    env, roots = "GEMINI_DATA_DIR", (".gemini/tmp",)
    globs, project_level, policy = ("*/chats/*.json", "*/chats/*.jsonl"), 2, "last"

    def is_whole(self, path):  # older chats are one JSON file, newer ones JSONL
        return path.suffix == ".json"

    def parse(self, entry, lines):
        entry["items"] += fmt.parse_gemini_lines(lines, entry["state"])

    def parse_whole(self, data, mtime):
        return fmt.parse_gemini(data, mtime)


class Qwen(FileSource):
    id, name, aliases = "qwen", "Qwen Code", ("qwen-code",)
    env, roots = "QWEN_DATA_DIR", (".qwen",)
    globs = ("projects/*/chats/*.jsonl", "projects/*/chats/archive/*.jsonl")

    def parse(self, entry, lines):
        entry["items"] += fmt.parse_qwen(lines)

    def project(self, path, entry):
        if entry.get("project"):
            return _project_name(entry["project"])
        folder = path.parents[2] if path.parent.name == "archive" else path.parents[1]
        return _project_name(folder.name)


class Copilot(FileSource):
    id, name, aliases = "copilot", "Copilot CLI", ("github-copilot", "copilot-cli", "gh-copilot")
    env, roots = "COPILOT_HOME", (".copilot",)
    globs, project_level = ("session-state/*/events.jsonl",), None

    def parse(self, entry, lines):
        entry["items"] += fmt.parse_copilot(lines, entry["state"])


class Amp(FileSource):
    id, name = "amp", "Amp"
    env, roots = "AMP_DATA_DIR", ("$DATA/amp",)
    globs, whole, project_level = ("threads/**/*.json",), True, None

    def parse_whole(self, data, mtime):
        return fmt.parse_amp(data)


class Droid(FileSource):
    id, name, aliases = "droid", "Droid", ("factory", "factory-droid")
    env, roots = "DROID_SESSIONS_DIR", (".factory/sessions",)
    globs, whole, totals, project_level = ("**/*.settings.json",), True, True, None

    def parse_whole(self, data, mtime):
        return fmt.droid_totals(data)


class Pi(FileSource):
    id, name, aliases = "pi", "pi", ("pi-agent", "pi-coding-agent")
    env, roots = "PI_AGENT_DIR", (".pi/agent",)
    globs = ("sessions/*/*.jsonl",)

    def parse(self, entry, lines):
        entry["items"] += fmt.parse_pi(lines, entry["state"])


class OhMyPi(Pi):
    id, name, aliases = "omp", "Oh My Pi", ("oh-my-pi", "ohmypi")
    env, roots = "", (".omp/agent",)


class OpenClaw(Pi):
    """OpenClaw writes pi-style JSONL per session, and newer versions an SQLite database per agent
    (`openclaw doctor --fix` migrates the JSONL into it and leaves the files): the database wins."""

    id, name, aliases = "openclaw", "OpenClaw", ("clawdbot", "moltbot")
    env, roots = "OPENCLAW_DIR", (".openclaw", ".clawdbot", ".moltbot", ".moldbot")
    globs, project_level = ("agents/*/sessions/*.jsonl",), None

    def __init__(self, home):
        super().__init__(home)
        self._cache: dict[Path, tuple] = {}

    def files(self, root):
        return [p for p in super().files(root) if not (p.parents[1] / "agent" / "openclaw-agent.sqlite").exists()]

    def collect(self, since, now):
        usages, working = super().collect(since, now)
        for root in self.dirs():
            for path in root.glob("agents/*/agent/openclaw-agent.sqlite") if root.is_dir() else []:
                mtime = _db_mtime(path)
                if mtime < since:
                    continue
                stamp = _db_stamp(path)
                if self._cache.get(path, (None,))[0] != stamp:
                    self._cache[path] = (stamp, self._read(path, since))
                usages += self._cache[path][1]
                if now - mtime < ACTIVE_SECONDS:
                    working.append("a session")
        return merge(usages, self.policy), working

    def _read(self, path: Path, since: float) -> list[Usage]:
        db = _open_db(path)
        if db is None:
            return []
        try:
            rows = db.execute("SELECT event_json, created_at FROM transcript_events WHERE created_at >= ?",
                              (int(since * 1000),)).fetchall()
        except sqlite3.Error:
            return []
        finally:
            db.close()
        lines = []
        for event, created in rows:
            try:
                record = json.loads(event)
            except (TypeError, ValueError):
                continue
            if isinstance(record, dict):
                record.setdefault("timestamp", created)
                lines.append(json.dumps(record))
        return fmt.parse_pi(lines, {})


class Grok(FileSource):
    id, name, aliases = "grok", "Grok Build", ("grok-build", "grok-cli")
    env, roots = "GROK_HOME", (".grok",)
    globs = ("sessions/*/*/updates.jsonl",)

    def parse(self, entry, lines):
        entry["items"] += fmt.parse_grok(lines)

    def project(self, path, entry):
        return _project_name(entry.get("project") or unquote(path.parents[1].name))


class Kimi(FileSource):
    id, name, aliases = "kimi", "Kimi Code", ("kimi-cli", "kimi-code")
    env, roots = "KIMI_DATA_DIR", (".kimi", ".kimi-code")
    globs, project_level = ("sessions/*/*/wire.jsonl", "sessions/*/*/agents/*/wire.jsonl"), None

    def parse(self, entry, lines):
        entry["items"] += fmt.parse_kimi(lines, entry["state"].get("model", ""))

    def collect(self, since, now):
        for root in self.dirs():  # the configured model, for lines that don't name theirs
            try:
                model = json.loads((root / "config.json").read_text()).get("model", "")
            except (OSError, ValueError, AttributeError):
                continue
            for path, entry in self.reader.files.items():
                if root in path.parents and isinstance(model, str):
                    entry["state"]["model"] = model
        return super().collect(since, now)


class DeepSeek(FileSource):
    """DeepSeek Harness keeps one log per session, rewritten as session.v<N>.jsonl when its format
    moves on: only the newest generation of each session counts."""

    id, name, aliases = "dsh", "DeepSeek Harness", ("deepseek", "deepseek-harness")
    env, roots = "DSH_HOME", (".dsh",)
    globs, project_level = ("sessions/*/*/session*.jsonl", "sessions/*/*/session*.jsonl.zst",
                            "sessions/*/*/session*.jsonl.zstd"), 2

    def files(self, root):
        newest: dict[Path, tuple] = {}
        for path in super().files(root):
            found = re.fullmatch(r"session(?:\.v(\d+))?\.jsonl(\.zstd?)?", path.name)
            if found:
                rank = (int(found.group(1) or 0), not found.group(2))  # newest version; plain over compressed
                if path.parent not in newest or rank > newest[path.parent][0]:
                    newest[path.parent] = (rank, path)
        return [path for _rank, path in newest.values()]

    def parse(self, entry, lines):
        entry["items"] += fmt.parse_dsh(lines, entry["state"])


class Cline(FileSource):
    id, name, aliases = "cline", "Cline", ("cline-cli", "claude-dev")
    env = "CLINE_DATA_DIR"
    roots = (".cline",) + tuple(f"$CONFIG/{editor}/User/globalStorage/saoudrizwan.claude-dev" for editor in
                                ("Code", "Code - Insiders", "VSCodium", "Cursor", "Windsurf"))
    globs = ("tasks/*/ui_messages.json", "data/tasks/*/ui_messages.json",
             "data/sessions/*/*.messages.json", "sessions/*/*.messages.json")
    whole, project_level = True, None

    def parse_whole(self, data, mtime):
        return fmt.parse_cline(data)


class Codebuff(FileSource):
    id, name, aliases = "codebuff", "Codebuff", ("manicode", "freebuff")
    env, roots = "CODEBUFF_DATA_DIR", ("$CONFIG/manicode", "$CONFIG/manicode-dev", "$CONFIG/manicode-staging")
    globs, whole, project_level = ("projects/*/chats/*/chat-messages.json",), True, 3

    def is_whole(self, path):
        return True

    def parse_whole(self, data, mtime):
        return fmt.parse_codebuff(data, mtime)


class OpenCode(Source):
    """OpenCode's SQLite database. OpenCode 2 writes `session_message` (assistant and completed
    compaction rows; the folder is on `session_v2`); 1.x wrote `message` (and `session`), whose rows
    a migration copies into the new table with the same ids. A forked session starts with copies of
    its parent's rows up to `fork_boundary`; those are the parent's. OpenWork runs on OpenCode and
    lands in the same file."""

    id, name, aliases = "opencode", "OpenCode", ("open-code", "openwork")
    env, roots = "OPENCODE_DATA_DIR", ("$DATA/opencode",)
    database = "opencode.db"

    def __init__(self, home):
        super().__init__(home)
        self._cache: dict[Path, tuple] = {}

    def collect(self, since, now):
        usages, working = [], []
        for root in self.dirs():
            path = root / self.database
            if not path.is_file() or _db_mtime(path) < since:
                continue
            stamp = _db_stamp(path)
            if self._cache.get(path, (None,))[0] != stamp:
                self._cache[path] = (stamp, *self._read(path, since))
            _, found, busy = self._cache[path]
            usages += found
            working += [p for at, p in busy if now - at < ACTIVE_SECONDS]
        return merge(usages, self.policy), working

    def _read(self, path: Path, since: float) -> tuple[list[Usage], list]:
        db = _open_db(path)
        if db is None:
            return [], []
        try:
            return self._read_db(db, int(since * 1000))
        except sqlite3.Error:
            return [], []
        finally:
            db.close()

    def _read_db(self, db, since_ms: int) -> tuple[list[Usage], list]:
        usages, busy, seen = [], [], set()
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "session_message" in tables:
            folders, cutoffs = {}, {}
            if "session_v2" in tables:
                cols = _columns(db, "session_v2")
                for row in db.execute("SELECT * FROM session_v2 WHERE time_updated >= ?", (since_ms,)):
                    folders[row["id"]] = row["directory"] if "directory" in cols else ""
                    if {"time_busy", "time_idle"} <= cols and (row["time_busy"] or 0) > (row["time_idle"] or 0):
                        busy.append((time.time(), _project_name(folders[row["id"]] or "") or "a session"))
                if {"fork_session_id", "fork_boundary"} <= cols:
                    cutoffs = self._fork_cutoffs(db)
            for row in db.execute("SELECT id, session_id, type, seq, time_created, data FROM session_message "
                                  "WHERE type IN ('assistant', 'compaction') AND time_updated >= ?", (since_ms,)):
                if row["session_id"] in cutoffs and (row["seq"] or 0) <= cutoffs[row["session_id"]]:
                    continue  # a fork's copy of its parent's history
                try:
                    data = json.loads(row["data"])
                except (TypeError, ValueError):
                    continue
                if row["type"] == "compaction" and data.get("status") != "completed":
                    continue
                usage = fmt.opencode_message(data, row["time_created"])
                seen.add(row["id"])
                if usage is not None:
                    usage.key = ("opencode", row["id"])
                    usages.append(usage)
        if "message" in tables and {"data", "time_updated"} <= _columns(db, "message"):
            folders = {}
            if "session" in tables and "directory" in _columns(db, "session"):
                folders = {r[0]: r[1] for r in db.execute("SELECT id, directory FROM session")}
            for row in db.execute("SELECT id, session_id, time_updated, data FROM message WHERE time_updated >= ?",
                                  (since_ms,)):
                if row["id"] in seen:
                    continue
                try:
                    data = json.loads(row["data"])
                except (TypeError, ValueError):
                    continue
                if not isinstance(data, dict) or data.get("role") != "assistant":
                    continue
                usage = fmt.opencode_message(data, row["time_updated"])
                if usage is not None:
                    usage.key = ("opencode", row["id"])
                    usages.append(usage)
                    if "session_v2" not in tables and time.time() - row["time_updated"] / 1000 < ACTIVE_SECONDS:
                        busy.append((row["time_updated"] / 1000,
                                     _project_name(folders.get(row["session_id"]) or "") or "a session"))
        return usages, busy

    @staticmethod
    def _fork_cutoffs(db) -> dict[str, int]:
        """fork session id -> the last seq that is a copy of its parent's history."""
        out = {}
        for fork, parent, boundary in db.execute(
                "SELECT id, fork_session_id, fork_boundary FROM session_v2 "
                "WHERE fork_session_id IS NOT NULL AND fork_boundary IS NOT NULL"):
            try:
                boundary = json.loads(boundary)
                found = db.execute("SELECT seq FROM session_message WHERE session_id = ? AND id = ?",
                                   (parent, boundary["messageID"])).fetchone()
            except (TypeError, ValueError, KeyError, sqlite3.Error):
                continue
            if found is None or found[0] is None:
                continue
            if boundary.get("type") == "through":
                out[fork] = found[0]
            else:  # up to, but not including, the boundary message
                before = db.execute("SELECT MAX(seq) FROM session_message WHERE session_id = ? AND seq < ?",
                                    (parent, found[0])).fetchone()
                if before and before[0] is not None:
                    out[fork] = before[0]
        return out


class Kilo(OpenCode):
    """Kilo (an OpenCode fork) keeps OpenCode 1.x's `message` table."""

    id, name, aliases = "kilo", "Kilo", ("kilocode", "kilo-code", "kilo-cli")
    env, roots, database = "KILO_DATA_DIR", ("$DATA/kilo",), "kilo.db"

    def _read_db(self, db, since_ms):
        usages, busy = [], []
        cols = _columns(db, "message")
        if not {"id", "data"} <= cols:
            return [], []
        for row in db.execute("SELECT id, data FROM message"):
            try:
                data = json.loads(row["data"])
            except (TypeError, ValueError):
                continue
            if not isinstance(data, dict) or data.get("role") not in (None, "assistant"):
                continue
            usage = fmt.opencode_message(data)
            if usage is not None and usage.at.timestamp() * 1000 >= since_ms:
                usage.key = ("kilo", row["id"])
                usages.append(usage)
                if time.time() - usage.at.timestamp() < ACTIVE_SECONDS:
                    busy.append((usage.at.timestamp(), "a session"))
        return usages, busy


class TableSource(Source):
    """A SQLite table with a row per session holding its running totals. Columns are found by name
    (the first of each list that exists and isn't NULL), so a renamed or missing one reads as zero.
    What a row's totals grew by between two looks counts at the time of the look."""

    files: tuple[str, ...] = ()
    table = ""
    columns: dict[str, tuple[str, ...]] = {}

    def __init__(self, home):
        super().__init__(home)
        self._cache: dict[Path, tuple] = {}
        self._totals: dict = {}  # (file, row id) -> the last running total seen
        self._grown: dict = {}  # (file, row id) -> usage counted from it so far
        self._latest: dict[Path, str] = {}  # file -> project of the newest row

    def collect(self, since, now):
        usages, working = [], []
        for root in self.dirs():
            for name in self.files:
                path = root / name
                if not path.is_file() or _db_mtime(path) < since:
                    continue
                stamp = _db_stamp(path)
                if self._cache.get(path, (None,))[0] != stamp:
                    self._cache[path] = (stamp, self._read(path, since))
                usages += self._cache[path][1]
                if now - _db_mtime(path) < ACTIVE_SECONDS:
                    working.append(self._latest.get(path, "") or "a session")
        return usages, working

    def model_of(self, row) -> str:
        return str(self._value(row, "model") or "")

    def _value(self, row, kind: str):
        for column in self.columns.get(kind, ()):
            if column in row.keys() and row[column] is not None:
                return row[column]
        return None

    def _read(self, path: Path, since: float) -> list[Usage]:
        db = _open_db(path)
        if db is None:
            return []
        try:
            present = _columns(db, self.table)
            if not present:
                return []
            rows = db.execute(f'SELECT * FROM "{self.table}"').fetchall()
        except sqlite3.Error:
            return []
        finally:
            db.close()
        mtime = _time(_db_mtime(path))
        newest = None
        for row in rows:
            at = _time(self._value(row, "time"))
            if at is None:
                continue
            inp, outp = fmt.num(self._value(row, "input")), fmt.num(self._value(row, "output"))
            total = fmt.num(self._value(row, "total"))
            cost = self._value(row, "cost")
            usage = Usage(at, inp, outp, fmt.num(self._value(row, "cache_read")),
                          fmt.num(self._value(row, "cache_write")), self.model_of(row),
                          fmt.remainder(total, inp, outp), float(cost) if isinstance(cost, (int, float)) else 0.0)
            key = (path, self._value(row, "id"))
            before = self._totals.get(key)
            self._totals[key] = usage
            if before is None and at.timestamp() < since:
                continue  # an older session: its totals so far are the baseline, growth from here counts
            kept = [u for u in self._grown.get(key, []) if u.at.timestamp() >= since]
            self._grown[key] = kept + _growth(before, usage, at if before is None else mtime)
            if newest is None or at > newest[0]:
                newest = (at, _project_name(str(self._value(row, "project") or "")))
        self._latest[path] = newest[1] if newest else ""
        return [u for key, items in self._grown.items() if key[0] == path for u in items]


class Goose(TableSource):
    id, name, aliases = "goose", "Goose", ("goose-cli", "block-goose")
    env = "GOOSE_PATH_ROOT"
    roots = ("$DATA/goose/sessions", "$DATA/Block/goose/sessions")
    files, table = ("sessions.db",), "sessions"
    columns = {
        "input": ("accumulated_input_tokens", "input_tokens"),
        "output": ("accumulated_output_tokens", "output_tokens"),
        "total": ("accumulated_total_tokens", "total_tokens"),
        "project": ("working_dir",),
        "id": ("id",),
        "time": ("updated_at", "created_at"),
    }

    def dirs(self):
        if os.environ.get(self.env):
            return [Path(os.environ[self.env]).expanduser() / "data" / "sessions"]
        return super().dirs()

    def model_of(self, row):
        try:
            return str(json.loads(self._value(row, "config") or row["model_config_json"] or "{}").get("model_name", ""))
        except (ValueError, TypeError, AttributeError, IndexError, KeyError):
            return ""


class Hermes(TableSource):
    """Hermes's reasoning tokens are part of its output, so they aren't added; its cost is the
    provider's actual one when recorded, else its estimate."""

    id, name, aliases = "hermes", "Hermes", ("hermes-agent",)
    env, roots = "HERMES_HOME", (".hermes",)
    files, table = ("state.db",), "sessions"
    columns = {
        "input": ("input_tokens",),
        "output": ("output_tokens",),
        "cache_read": ("cache_read_tokens",),
        "cache_write": ("cache_write_tokens",),
        "cost": ("actual_cost_usd", "estimated_cost_usd"),
        "model": ("model",),
        "id": ("id",),
        "time": ("updated_at", "ended_at", "started_at"),
    }


class ZCode(Source):
    """ZCode's model_usage table: a row per completed model call. Its input includes the cache
    buckets; the session table has the folder."""

    id, name = "zcode", "ZCode"
    env, roots = "ZCODE_HOME", (".zcode/cli/db",)

    def __init__(self, home):
        super().__init__(home)
        self._cache: dict[Path, tuple] = {}

    def dirs(self):
        if os.environ.get(self.env):
            return [Path(os.environ[self.env]).expanduser() / "cli" / "db"]
        return super().dirs()

    def collect(self, since, now):
        usages, working = [], []
        for root in self.dirs():
            path = root / "db.sqlite"
            if not path.is_file() or _db_mtime(path) < since:
                continue
            stamp = _db_stamp(path)
            if self._cache.get(path, (None,))[0] != stamp:
                self._cache[path] = (stamp, self._read(path, since))
            usages += self._cache[path][1]
            if now - _db_mtime(path) < ACTIVE_SECONDS:
                working.append("a session")
        return usages, working

    def _read(self, path: Path, since: float) -> list[Usage]:
        db = _open_db(path)
        if db is None:
            return []
        try:
            cols = _columns(db, "model_usage")
            if not {"started_at", "input_tokens", "output_tokens"} <= cols:
                return []
            pick = lambda c: f"m.{c}" if c in cols else "0"  # noqa: E731
            total = "m.computed_total_tokens" if "computed_total_tokens" in cols else "m.input_tokens + m.output_tokens"
            status = "AND m.status = 'completed'" if "status" in cols else ""
            rows = db.execute(f"SELECT m.id, m.started_at, {pick('model_id')}, m.input_tokens, m.output_tokens, "
                              f"{pick('cache_creation_input_tokens')}, {pick('cache_read_input_tokens')}, {total} "
                              f"FROM model_usage m WHERE m.started_at >= ? {status}", (int(since * 1000),)).fetchall()
        except sqlite3.Error:
            return []
        finally:
            db.close()
        out = []
        for rid, started, model, inp, outp, write, read, total in rows:
            at = _time(started)
            inp, outp, total = fmt.num(inp), fmt.num(outp), fmt.num(total)
            read = min(fmt.num(read), inp)
            write = min(fmt.num(write), inp - read)
            usage = Usage(at, inp - read - write, outp, read, write, str(model or ""),
                          fmt.remainder(total, inp, outp), key=("zcode", rid))
            if at is not None and not usage.empty:
                out.append(usage)
        return out


class Antigravity(Source):
    """Antigravity keeps a database per conversation; each row of gen_metadata is a protobuf
    message describing one model call (see agent_formats.parse_antigravity)."""

    id, name, aliases = "antigravity", "Antigravity", ("agy", "antigravity-cli")
    env = "ANTIGRAVITY_DATA_DIR"
    roots = (".gemini/antigravity", ".gemini/antigravity-cli", ".gemini/antigravity-ide",
             ".gemini/antigravity-backup", ".gemini/antigravity-acp", "$CONFIG/antigravity")

    def __init__(self, home):
        super().__init__(home)
        self._cache: dict[Path, tuple] = {}

    def collect(self, since, now):
        usages, working = [], []
        for root in self.dirs():
            folder = root / "conversations"
            if not folder.is_dir():
                continue
            for path in folder.glob("*.db"):
                mtime = _db_mtime(path)
                if mtime < since:
                    continue
                stamp = _db_stamp(path)
                if self._cache.get(path, (None,))[0] != stamp:
                    self._cache[path] = (stamp, self._read(path))
                usages += self._cache[path][1]
                if now - mtime < ACTIVE_SECONDS:
                    working.append("a session")
        return [u for u in merge(usages, self.policy) if u.at.timestamp() >= since], working

    def _read(self, path: Path) -> list[Usage]:
        db = _open_db(path)
        if db is None:
            return []
        try:
            rows = db.execute("SELECT data FROM gen_metadata ORDER BY idx").fetchall()
        except sqlite3.Error:
            return []
        finally:
            db.close()
        out = []
        for (data,) in rows:
            if isinstance(data, (bytes, bytearray)):
                out += fmt.parse_antigravity(bytes(data))
        return out


# -- agents seen only as a running process -------------------------------------------------
_INTERPRETERS = {"node", "nodejs", "bun", "deno", "python", "python3", "pypy3", "uv", "uvx", "npx"}
BUSY_CPU_SECONDS = 0.3  # CPU time between two looks that counts as working


@dataclass
class Process:
    pid: int
    argv: list[str]
    cwd: str
    cpu: float  # seconds of CPU used so far


def program_of(argv: list[str]) -> str:
    """The program a command line runs: 'aider' for `python3 /usr/bin/aider --model x`."""
    if not argv:
        return ""
    name = os.path.basename(argv[0])
    if re.sub(r"[\d.]+$", "", name) in _INTERPRETERS:
        for arg in argv[1:]:
            if not arg.startswith("-"):
                return os.path.basename(arg).removesuffix(".js").removesuffix(".py")
        return ""
    return name


def cpu_seconds(stat: str, ticks: int = 100) -> float:
    """utime + stime from /proc/PID/stat (the name in parentheses may hold spaces)."""
    fields = stat.rsplit(")", 1)[-1].split()
    try:
        return (int(fields[11]) + int(fields[12])) / ticks
    except (IndexError, ValueError):
        return 0.0


def scan_processes(root: Path = Path("/proc")) -> list[Process]:
    """This user's processes, with what they run and where."""
    try:
        ticks = os.sysconf("SC_CLK_TCK")
    except (ValueError, OSError):
        ticks = 100
    uid, out = os.getuid(), []
    try:
        entries = [e for e in os.scandir(root) if e.name.isdigit()]
    except OSError:
        return []
    for entry in entries:
        try:
            if entry.stat().st_uid != uid:
                continue
            argv = [a.decode("utf-8", "replace") for a in
                    (Path(entry.path) / "cmdline").read_bytes().split(b"\0") if a]
            if not argv:
                continue
            cwd = os.readlink(Path(entry.path) / "cwd")
            stat = (Path(entry.path) / "stat").read_text()
        except OSError:
            continue
        out.append(Process(int(entry.name), argv, cwd, cpu_seconds(stat, ticks)))
    return out


class ProcessSource(Source):
    """An agent known by its program name only. `programs` are executable names; `markers` are
    parts of a script path (for npm/pip installs run as `node .../@scope/cli/...`)."""

    programs: tuple[str, ...] = ()
    markers: tuple[str, ...] = ()

    def __init__(self, home):
        super().__init__(home)
        self.processes: Callable[[], list[Process]] = lambda: []
        self._cpu: dict[int, float] = {}
        self.running: list[str] = []

    def available(self) -> bool:
        return any(shutil.which(p) for p in self.programs)

    def matches(self, argv: list[str]) -> bool:
        if program_of(argv) in self.programs:
            return True
        return bool(self.markers) and any(m in arg for arg in argv[:3] for m in self.markers)

    def collect(self, since, now):
        working, running, cpu = [], [], {}
        for proc in self.processes():
            if not self.matches(proc.argv):
                continue
            project = _project_name(proc.cwd) or "a session"
            cpu[proc.pid] = proc.cpu
            before = self._cpu.get(proc.pid)
            busy = before is not None and proc.cpu - before >= BUSY_CPU_SECONDS
            (working if busy else running).append(project)
        self._cpu = cpu
        self.running = sorted(set(running) - set(working))
        return [], working


class CursorAgent(ProcessSource):
    id, name, aliases = "cursor", "Cursor CLI", ("cursor-agent", "cursor-cli")
    programs = ("cursor-agent",)


class Aider(ProcessSource):
    id, name, aliases = "aider", "Aider", ("aider-chat",)
    programs = ("aider",)


class Kiro(ProcessSource):
    id, name, aliases = "kiro", "Kiro CLI", ("kiro-cli",)
    programs = ("kiro-cli", "kiro")


class Auggie(ProcessSource):
    id, name, aliases = "auggie", "Auggie", ("augment", "augment-code")
    programs, markers = ("auggie",), ("@augmentcode/auggie",)


class Crush(ProcessSource):
    id, name, aliases = "crush", "Crush", ("charm-crush",)
    programs = ("crush",)


class Continue(ProcessSource):
    id, name, aliases = "continue", "Continue CLI", ("cn", "continue-cli")
    markers = ("@continuedev/cli",)  # its program is called `cn`, too short to trust alone


class OpenHands(ProcessSource):
    id, name, aliases = "openhands", "OpenHands", ("openhands-cli",)
    programs = ("openhands",)


class MistralVibe(ProcessSource):
    id, name, aliases = "vibe", "Mistral Vibe", ("mistral-vibe",)
    programs, markers = ("mistral-vibe",), ("mistral_vibe", "mistral-vibe")


class Junie(ProcessSource):
    id, name = "junie", "Junie"
    programs = ("junie",)


class Plandex(ProcessSource):
    id, name = "plandex", "Plandex"
    programs = ("plandex", "pdx")


SOURCES: tuple[type[Source], ...] = (
    Claude, Codex, OpenCode, Kilo, Gemini, Qwen, Antigravity, Copilot, Amp, Droid, Pi, OhMyPi, OpenClaw,
    Grok, Kimi, Goose, Hermes, Cline, Codebuff, ZCode, DeepSeek,
    CursorAgent, Aider, Kiro, Auggie, Crush, Continue, OpenHands, MistralVibe, Junie, Plandex,
)


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


# -- what the agents say they are doing ----------------------------------------------------
STATES = {
    "working": "working", "start": "working", "busy": "working", "running": "working", "prompt": "working",
    "waiting": "waiting", "blocked": "waiting", "input": "waiting", "attention": "waiting",
    "permission": "waiting", "notify": "waiting", "notification": "waiting",
    "done": "done", "stop": "done", "stopped": "done", "finished": "done", "complete": "done",
    "idle": "idle", "end": "idle", "exit": "idle", "clear": "idle",
}
# how long a state holds without news (a crashed agent never says it stopped)
EXPIRES = {"working": 30 * 60, "waiting": 2 * 3600, "done": 10 * 60}


@dataclass
class Report:
    agent: str
    project: str
    state: str
    at: float
    source: str = "hook"


def parse_report(args: list[str], cwd: str = "") -> dict:
    """`agent STATE [--agent NAME] [--project DIR]` (also `--agent=NAME`) -> a report as a dict."""
    usage = "usage: cornerwindow agent working|waiting|done|idle [--agent NAME] [--project DIR]"
    state, agent, project = None, "agent", cwd
    items = list(args)
    while items:
        item = items.pop(0)
        key, _, value = item.partition("=")
        if key in ("--agent", "-a", "--project", "-p"):
            if not value:
                if not items:
                    raise ValueError(usage)
                value = items.pop(0)
            if key in ("--agent", "-a"):
                agent = value
            else:
                project = value
        elif state is None and _norm(item) in STATES:
            state = STATES[_norm(item)]
        elif item.lstrip().startswith("{"):
            continue  # Codex's `notify` adds the event as JSON: the state is in our own arguments
        else:
            raise ValueError(usage)
    if state is None:
        raise ValueError(usage)
    return {"agent": agent.strip() or "agent", "state": state, "project": _project_name(project) or ""}


class AgentReports:
    """States agents report through their hooks. Kept in memory: a restart forgets them."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], Report] = {}
        self._lock = threading.Lock()

    def update(self, agent: str, state: str, project: str = "", at: float | None = None) -> None:
        key = (agent, project)
        with self._lock:
            if state == "idle":
                self._items.pop(key, None)
            else:
                self._items[key] = Report(agent, project, state, time.time() if at is None else at)

    def current(self, now: float | None = None) -> list[Report]:
        now = time.time() if now is None else now
        with self._lock:
            for key, r in list(self._items.items()):
                if now - r.at > EXPIRES.get(r.state, 0):
                    del self._items[key]
            return list(self._items.values())


REPORTS = AgentReports()


def herdr_sessions(home: Path | None = None) -> list[str | None]:
    """herdr sessions with a server up: None for the default one, else the session's name. Each
    keeps its socket in herdr's config folder (the default at the top, named ones under sessions/)."""
    config = Path(os.environ.get("XDG_CONFIG_HOME") or (home or Path.home()) / ".config") / "herdr"
    out: list[str | None] = []
    if (config / "herdr.sock").exists():
        out.append(None)
    try:
        out += sorted(p.parent.name for p in config.glob("sessions/*/herdr.sock"))
    except OSError:
        pass
    return out


def herdr_states(run=subprocess.run, sessions=herdr_sessions) -> list[Report]:
    """Agents running in herdr panes, in every running herdr session, and what they're doing."""
    if not shutil.which("herdr"):
        return []
    out = []
    for session in sessions():
        command = ["herdr", "agent", "list"] if session is None else ["herdr", "--session", session, "agent", "list"]
        try:
            done = run(command, capture_output=True, text=True, timeout=2)
        except (OSError, subprocess.SubprocessError):
            continue
        out += parse_herdr(done.stdout)
    return out


def parse_herdr(text: str) -> list[Report]:
    try:
        data = json.loads(text or "null")
    except ValueError:
        return []
    agents = _find_key(data, "agents", 3) if isinstance(data, dict) else data
    out = []
    for info in agents if isinstance(agents, list) else []:
        if not isinstance(info, dict) or not info.get("agent"):
            continue
        state = {"working": "working", "blocked": "waiting", "done": "done"}.get(info.get("agent_status"))
        if state:
            cwd = info.get("foreground_cwd") or info.get("cwd") or ""
            out.append(Report(str(info["agent"]), _project_name(cwd), state, time.time(), "herdr"))
    return out


# -- putting it together -------------------------------------------------------------------
class AgentUsage:
    def __init__(self, home: Path | None = None, reports: AgentReports | None = None,
                 herdr: Callable[[], list[Report]] | None = herdr_states,
                 processes: Callable[[], list[Process]] | None = scan_processes) -> None:
        self.home = home or Path.home()
        self.sources = [cls(self.home) for cls in SOURCES]
        self.reports = REPORTS if reports is None else reports
        self.herdr = herdr
        self.processes = processes
        self.hidden: set[str] = set()

    def configure(self, hide=(), herdr: bool = True, processes: bool = True) -> None:
        """`hide`: agent ids or names to leave out. `herdr`/`processes`: use those for states."""
        self.hidden = {_norm(n) for n in hide or () if str(n).strip()}
        self.herdr = herdr_states if herdr else None
        self.processes = scan_processes if processes else None

    def shown(self) -> list[Source]:
        return [s for s in self.sources if not (s.names() & self.hidden)
                and (self.processes or not isinstance(s, ProcessSource))]

    def available(self) -> bool:
        return any(s.available() for s in self.shown()) or bool(self.herdr and shutil.which("herdr"))

    def summaries(self, now: dt.datetime | None = None) -> list[AgentSummary]:
        now = now or dt.datetime.now().astimezone()
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        since = min(midnight, now - WINDOW).timestamp()
        states = [r for r in self.states() if _norm(r.agent) not in self.hidden]
        sources = self.shown()
        scanned = None

        def processes() -> list[Process]:  # one look at /proc per refresh, and only when needed
            nonlocal scanned
            if scanned is None:
                scanned = self.processes() if self.processes else []
            return scanned

        out = []
        for source in sources:
            mine = [r for r in states if _norm(r.agent) in source.names()]
            watch = isinstance(source, ProcessSource)
            if not mine and not watch and not source.available():
                continue
            if watch:
                source.processes = processes
            try:
                usages, working = source.collect(since, time.time())
            except Exception:  # one odd file must not hide every other agent
                usages, working = [], []
            summary = _summary(source.name, usages, now, midnight, working, mine, source.window,
                               getattr(source, "running", []))
            if summary is not None:
                summary.id, summary.limits = source.id, list(source.limits)
                out.append(summary)
        known = set().union(*(s.names() for s in self.sources))
        others: dict[str, list[Report]] = {}
        for r in states:
            if _norm(r.agent) not in known:
                others.setdefault(r.agent, []).append(r)
        for agent, mine in others.items():
            summary = _summary(_display(agent), [], now, midnight, [], mine, False)
            if summary is not None:
                summary.id = _norm(agent)
                out.append(summary)
        return out

    def states(self) -> list[Report]:
        """Hook reports win over herdr for the same agent and project."""
        reports = self.reports.current()
        seen = {(_norm(r.agent), r.project) for r in reports}
        extra = self.herdr() if self.herdr else []
        return reports + [r for r in extra if (_norm(r.agent), r.project) not in seen]


def _display(agent: str) -> str:
    return agent if agent != agent.lower() else agent.replace("-", " ").replace("_", " ").title()


def _summary(name: str, usages: list[Usage], now: dt.datetime, midnight: dt.datetime, working: list[str],
             states: list[Report] = (), window: bool = True, running: list[str] = ()) -> AgentSummary | None:
    if states:  # the agent says what it's doing: better than guessing from the log
        working = [r.project or "a session" for r in states if r.state == "working"]
    waiting = [r.project or "a session" for r in states if r.state == "waiting"]
    done = [r.project or "a session" for r in states if r.state == "done"]
    if not usages and not working and not waiting and not done and not running:
        return None
    out = AgentSummary(name, working=sorted(set(working)), waiting=sorted(set(waiting)),
                       done=sorted(set(done) - set(working) - set(waiting)),
                       running=sorted(set(running) - set(working) - set(waiting)))
    today = [u for u in usages if u.at >= midnight]
    out.today_tokens = sum(u.tokens for u in today)
    out.today_messages = len(today)
    out.today_cost = sum(u.cost for u in today)
    if window:
        found = current_window([u.at for u in usages], now)
        if found is not None:
            out.window_start, out.window_end = found
            out.window_tokens = sum(u.tokens for u in usages if found[0] <= u.at < found[1])
    out.models = sorted({_short_model(u.model) for u in today if u.model})
    out.last_seen = max((u.at for u in usages), default=None)
    return out


def _short_model(model: str) -> str:
    """claude-opus-5-5 -> Opus 5.5; anything else as it is."""
    model = model.rsplit("/", 1)[-1]
    parts = model.split("-")
    if len(parts) >= 3 and parts[0] == "claude":
        family = parts[1].capitalize()
        version = ".".join(p for p in parts[2:4] if p.isdigit())
        return f"{family} {version}".strip()
    return model


def human_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.0f}K"
    return str(n)


def human_cost(usd: float) -> str:
    return f"${usd:.2f}" if usd >= 0.01 else "<$0.01" if usd > 0 else ""
