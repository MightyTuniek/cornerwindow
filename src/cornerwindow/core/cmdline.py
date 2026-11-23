"""Reading what was typed into the command bar: shell lines, paths, URLs and search shortcuts
(pure and tested; the bar itself is modules/commandbar.py)."""

from __future__ import annotations

import os
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# ── shell: "> cmd" runs in the background, ">> cmd" in a terminal ─────────────────────────


def shell_line(text: str) -> tuple[str, str] | None:
    """('background' | 'terminal', command) for '> cmd' / '>> cmd'; None otherwise."""
    text = text.lstrip()
    if text.startswith(">>"):
        return "terminal", text[2:].strip()
    if text.startswith(">"):
        return "background", text[1:].strip()
    return None


# (program, arguments placed before the command to run)
TERMINALS: list[tuple[str, list[str]]] = [
    ("ptyxis", ["--"]), ("kgx", ["--"]), ("gnome-terminal", ["--"]), ("konsole", ["-e"]),
    ("kitty", []), ("alacritty", ["-e"]), ("foot", []), ("wezterm", ["start", "--"]), ("ghostty", ["-e"]),
    ("xfce4-terminal", ["-x"]), ("tilix", ["-e"]), ("terminator", ["-x"]), ("mate-terminal", ["-x"]),
    ("lxterminal", ["-e"]), ("x-terminal-emulator", ["-e"]), ("xterm", ["-e"]),
]


def terminal_argv(command: str, preferred: str, which: Callable[[str], str | None],
                  keep_open: bool = True) -> list[str] | None:
    """The argv that opens `command` in a terminal, or None if there is no terminal.
    `preferred` is a program name ('kitty') or '' to pick the first installed one."""
    known = dict(TERMINALS)
    if preferred:
        parts = preferred.split()
        program, extra = parts[0], parts[1:]
        prefix = extra or known.get(program, ["-e"])
        candidates = [(program, prefix)] if which(program) else []
    else:
        candidates = [(p, a) for p, a in TERMINALS if which(p)]
    if not candidates:
        return None
    program, prefix = candidates[0]
    script = command
    if keep_open:
        script = f'{command}\nprintf "\\n[exit %s] Press Enter to close " "$?"; read _'
    return [program, *prefix, "sh", "-c", script]


def first_word(text: str) -> str:
    return text.strip().split(" ", 1)[0] if text.strip() else ""


# ── paths: "~/Down" lists matches, Tab completes, Enter opens ─────────────────────────────


def looks_like_path(text: str) -> bool:
    text = text.strip()
    return text.startswith(("/", "~/")) or text == "~"


def expand(text: str) -> Path:
    return Path(os.path.expanduser(text.strip()))


@dataclass
class PathItem:
    path: Path
    is_dir: bool
    display: str  # with ~ for the home folder, dirs end in /


def tilde(path: Path | str) -> str:
    text, home = str(path), str(Path.home())
    if text == home:
        return "~"
    return "~" + text[len(home):] if text.startswith(home + os.sep) else text


def complete_path(text: str, limit: int = 6) -> list[PathItem]:
    """What '~/Do' could mean: the folder itself if it exists, else entries whose name starts
    with what's after the last slash (hidden ones only when that starts with a dot)."""
    raw = text.strip()
    target = expand(raw)
    if raw.endswith("/") and target.is_dir():
        folder, prefix = target, ""
    else:
        folder, prefix = target.parent, target.name
    items: list[PathItem] = []
    try:
        names = sorted(os.listdir(folder), key=str.lower)
    except OSError:
        names = []
    exact = None
    lower = prefix.lower()
    for name in names:
        if name.startswith(".") and not prefix.startswith("."):
            continue
        if not name.lower().startswith(lower):
            continue
        path = folder / name
        is_dir = path.is_dir()
        item = PathItem(path, is_dir, tilde(path) + ("/" if is_dir else ""))
        if name == prefix:
            exact = item
        else:
            items.append(item)
    items.sort(key=lambda i: (not i.is_dir, i.path.name.lower()))
    if exact is not None:
        items.insert(0, exact)
    return items[:limit]


# ── URLs: "github.com/foo", "https://…", "localhost:8000" ─────────────────────────────────
_TLDS = set("""com org net io dev app gov edu co uk de pl fr eu me ai gg tv xyz info nl se no fi dk es it
ch at be cz ru ua jp cn kr in br ca au nz us so sh to ly fm im is page site online tech blog news cloud""".split())
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://\S+$", re.I)
_HOST = re.compile(r"^(?:www\.)?([a-z0-9-]+\.)+([a-z]{2,})(:\d+)?(/\S*)?$", re.I)
_LOCAL = re.compile(r"^(localhost|127\.0\.0\.1|\[::1\]|\d{1,3}(?:\.\d{1,3}){3})(:\d+)(/\S*)?$", re.I)


def as_url(text: str) -> str | None:
    """A URL to open for what was typed, or None. Bare domains need a known ending (so
    'notes.txt' stays a search) unless they start with www."""
    text = text.strip()
    if not text or " " in text:
        return None
    if _SCHEME.match(text):
        return text
    if _LOCAL.match(text):
        return "http://" + text
    match = _HOST.match(text)
    if match and (text.lower().startswith("www.") or match.group(2).lower() in _TLDS):
        return "https://" + text
    return None


# ── search shortcuts: "yt lofi", "g python dataclass" ─────────────────────────────────────


def search_url(template: str, query: str) -> str:
    quoted = urllib.parse.quote_plus(query)
    return template.replace("{}", quoted) if "{}" in template else template + quoted


def engine_query(text: str, engines: dict[str, str]) -> tuple[str, str, str] | None:
    """(shortcut, url, query) when the first word is a search shortcut and something follows."""
    word, _, rest = text.strip().partition(" ")
    template = engines.get(word.lower())
    if not template or not rest.strip() or not isinstance(template, str):
        return None
    return word.lower(), search_url(template, rest.strip()), rest.strip()


def site_name(url: str) -> str:
    """'https://www.youtube.com/results?…' -> 'youtube.com'."""
    host = urllib.parse.urlparse(url).netloc or url
    return host.removeprefix("www.")


# ── files: ranking what a search found ────────────────────────────────────────────────────


def rank_files(query: str, paths: list[str], limit: int = 8) -> list[str]:
    """Best matches first: exact name, name starts with, word in name starts with, contains;
    shallower and shorter paths win ties."""
    words = query.lower().split()
    if not words:
        return []
    scored = []
    for path in paths:
        name = os.path.basename(path.rstrip("/")).lower()
        if not all(w in path.lower() for w in words):
            continue
        first = words[0]
        if name == query.lower():
            score = 100
        elif name.startswith(first):
            score = 80
        elif any(part.startswith(first) for part in re.split(r"[\s._-]+", name)):
            score = 60
        elif all(w in name for w in words):
            score = 40
        else:
            score = 10
        scored.append((-score, path.count("/"), len(path), path))
    scored.sort()
    seen, out = set(), []
    for *_rest, path in scored:
        if path not in seen:
            seen.add(path)
            out.append(path)
        if len(out) >= limit:
            break
    return out
