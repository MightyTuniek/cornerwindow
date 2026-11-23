"""Coding-agent usage from their own local logs (nothing is sent anywhere, no sign-in):

  Claude Code  ~/.claude/projects/<project>/<session>.jsonl   (per-message token usage)
  Codex        ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl   (token counts and plan limits)

Claude's plan limits are counted in 5-hour windows that start with your first message after a
break (rounded down to the hour, the way usage tools such as ccusage count them); Codex writes
its own limit percentages into the log. The parsers are pure and the readers incremental (each
file is read from where the last read stopped), so a big log costs little after the first look.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

WINDOW = dt.timedelta(hours=5)
ACTIVE_SECONDS = 90  # a log written to this recently = the agent is working


@dataclass
class Usage:
    at: dt.datetime
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    model: str = ""

    @property
    def tokens(self) -> int:
        """What counts against a plan: everything except cheap cache reads."""
        return self.input + self.output + self.cache_write


@dataclass
class Limit:
    used_percent: float
    window_minutes: int
    resets_at: dt.datetime | None


@dataclass
class AgentSummary:
    name: str
    today_tokens: int = 0
    today_messages: int = 0
    window_tokens: int = 0
    window_start: dt.datetime | None = None
    window_end: dt.datetime | None = None
    models: list[str] = field(default_factory=list)
    working: list[str] = field(default_factory=list)  # projects with a session writing right now
    limits: list[Limit] = field(default_factory=list)
    last_seen: dt.datetime | None = None


def _time(text: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None


# -- Claude Code ---------------------------------------------------------------------------
def parse_claude(lines, seen: set | None = None) -> list[Usage]:
    """Assistant messages with usage; a message streamed in several records counts once."""
    seen = set() if seen is None else seen
    out = []
    for line in lines:
        try:
            record = json.loads(line)
        except (ValueError, TypeError):
            continue
        if record.get("type") != "assistant":
            continue
        message = record.get("message") or {}
        usage = message.get("usage") or {}
        if not usage:
            continue
        key = (message.get("id"), record.get("requestId"))
        if key != (None, None):
            if key in seen:
                continue
            seen.add(key)
        at = _time(record.get("timestamp", ""))
        if at is None:
            continue
        out.append(Usage(at, int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0),
                         int(usage.get("cache_read_input_tokens") or 0),
                         int(usage.get("cache_creation_input_tokens") or 0), str(message.get("model") or "")))
    return out


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


# -- Codex ---------------------------------------------------------------------------------
def parse_codex(lines) -> tuple[list[Usage], list[Limit]]:
    usages, limits = [], []
    for line in lines:
        try:
            record = json.loads(line)
        except (ValueError, TypeError):
            continue
        payload = record.get("payload") or {}
        if payload.get("type") != "token_count":
            continue
        at = _time(record.get("timestamp", ""))
        info = payload.get("info") or {}
        last = info.get("last_token_usage") or {}
        if at is not None and last:
            usages.append(Usage(at, int(last.get("input_tokens") or 0) - int(last.get("cached_input_tokens") or 0),
                                int(last.get("output_tokens") or 0), int(last.get("cached_input_tokens") or 0)))
        rates = payload.get("rate_limits") or {}
        found = []
        for key in ("primary", "secondary"):
            rate = rates.get(key) or {}
            if "used_percent" not in rate:
                continue
            resets = None
            if rate.get("resets_at"):
                try:
                    resets = dt.datetime.fromtimestamp(float(rate["resets_at"]), dt.timezone.utc)
                except (TypeError, ValueError):
                    resets = _time(rate["resets_at"])
            elif rate.get("resets_in_seconds") is not None and at is not None:
                resets = at + dt.timedelta(seconds=float(rate["resets_in_seconds"]))
            found.append(Limit(float(rate["used_percent"]), int(rate.get("window_minutes") or 0), resets))
        if found:
            limits = found
    return usages, limits


# -- reading the logs ----------------------------------------------------------------------
class _Reader:
    """Remembers, per file, how far it has read and what it found."""

    def __init__(self) -> None:
        self.files: dict[Path, dict] = {}

    def read(self, path: Path, parse) -> list:
        try:
            stat = path.stat()
        except OSError:
            return []
        entry = self.files.setdefault(path, {"offset": 0, "items": [], "seen": set(), "limits": []})
        if stat.st_size < entry["offset"]:  # rewritten
            entry.update(offset=0, items=[], seen=set())
        if stat.st_size > entry["offset"]:
            with open(path, "rb") as handle:
                handle.seek(entry["offset"])
                data = handle.read()
            cut = data.rfind(b"\n") + 1  # keep a half-written last line for next time
            lines = data[:cut].decode("utf-8", "replace").splitlines()
            entry["offset"] += cut
            parse(entry, lines)
        return entry["items"]


class AgentUsage:
    def __init__(self, home: Path | None = None) -> None:
        self.home = home or Path.home()
        self._claude = _Reader()
        self._codex = _Reader()

    def claude_dir(self) -> Path:
        return Path(os.environ.get("CLAUDE_CONFIG_DIR") or self.home / ".claude") / "projects"

    def codex_dir(self) -> Path:
        return Path(os.environ.get("CODEX_HOME") or self.home / ".codex") / "sessions"

    def available(self) -> bool:
        return self.claude_dir().is_dir() or self.codex_dir().is_dir()

    def summaries(self, now: dt.datetime | None = None) -> list[AgentSummary]:
        now = now or dt.datetime.now().astimezone()
        out = []
        claude = self._summarize_claude(now)
        if claude is not None:
            out.append(claude)
        codex = self._summarize_codex(now)
        if codex is not None:
            out.append(codex)
        return out

    def _recent(self, folder: Path, pattern: str, since: float) -> list[Path]:
        try:
            return [p for p in folder.glob(pattern) if p.stat().st_mtime >= since]
        except OSError:
            return []

    def _summarize_claude(self, now: dt.datetime) -> AgentSummary | None:
        folder = self.claude_dir()
        if not folder.is_dir():
            return None
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        since = min(midnight, now - WINDOW).timestamp()

        def parse(entry, lines) -> None:
            entry["items"] += parse_claude(lines, entry["seen"])

        usages: list[Usage] = []
        working = []
        for path in self._recent(folder, "*/*.jsonl", since):
            usages += self._claude.read(path, parse)
            if time.time() - path.stat().st_mtime < ACTIVE_SECONDS:
                working.append(_project_name(path.parent.name))
        return _summary("Claude Code", usages, now, midnight, working)

    def _summarize_codex(self, now: dt.datetime) -> AgentSummary | None:
        folder = self.codex_dir()
        if not folder.is_dir():
            return None
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        since = min(midnight, now - WINDOW).timestamp()

        def parse(entry, lines) -> None:
            usages, limits = parse_codex(lines)
            entry["items"] += usages
            if limits:
                entry["limits"] = limits

        usages: list[Usage] = []
        limits: list[Limit] = []
        working = []
        newest = 0.0
        for path in self._recent(folder, "*/*/*/*.jsonl", since):
            usages += self._codex.read(path, parse)
            mtime = path.stat().st_mtime
            if mtime > newest and self._codex.files.get(path, {}).get("limits"):
                newest, limits = mtime, self._codex.files[path]["limits"]
            if time.time() - mtime < ACTIVE_SECONDS:
                working.append("a session")
        summary = _summary("Codex", usages, now, midnight, working)
        if summary is not None:
            summary.limits = limits
        return summary


def _summary(name: str, usages: list[Usage], now: dt.datetime, midnight: dt.datetime,
             working: list[str]) -> AgentSummary | None:
    if not usages and not working:
        return None
    out = AgentSummary(name, working=sorted(set(working)))
    today = [u for u in usages if u.at >= midnight]
    out.today_tokens = sum(u.tokens for u in today)
    out.today_messages = len(today)
    window = current_window([u.at for u in usages], now)
    if window is not None:
        out.window_start, out.window_end = window
        out.window_tokens = sum(u.tokens for u in usages if window[0] <= u.at < window[1])
    out.models = sorted({_short_model(u.model) for u in today if u.model})
    out.last_seen = max((u.at for u in usages), default=None)
    return out


def _short_model(model: str) -> str:
    """claude-opus-5-5 -> Opus 5.5; anything else as it is."""
    parts = model.split("-")
    if len(parts) >= 3 and parts[0] == "claude":
        family = parts[1].capitalize()
        version = ".".join(p for p in parts[2:4] if p.isdigit())
        return f"{family} {version}".strip()
    return model


def _project_name(folder: str) -> str:
    """'-home-user-cornerwindow' -> 'cornerwindow'."""
    return folder.strip("-").rsplit("-", 1)[-1] or folder


def human_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.0f}K"
    return str(n)
