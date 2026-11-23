"""Finding files by name for 'f query' in the command bar. Uses plocate/locate when installed
(instant, whole disk), otherwise walks the home folder with a time and size budget. Call it off
the main thread."""

from __future__ import annotations

import os
import time
from pathlib import Path

from ..core.cmdline import rank_files
from . import proc

SKIP_DIRS = {"node_modules", "__pycache__", "venv", ".venv", "site-packages", "dist-packages", "target",
             "build", ".git", ".cache", "snap", ".local", ".npm", ".cargo", ".rustup"}


def _hidden(path: str, home: str) -> bool:
    rest = path[len(home):] if path.startswith(home) else path
    return any(part.startswith(".") for part in rest.split("/") if part)


def _locate(query: str, home: str) -> list[str] | None:
    program = proc.which("plocate") or proc.which("locate")
    if not program:
        return None
    words = query.split()
    out = proc.run([program, "-i", "-l", "2000", "--", words[0]], timeout=2.5)
    if not out:
        return []
    return [line for line in out.splitlines()
            if line.startswith(home + "/") and not _hidden(line, home)
            and all(w.lower() in line.lower() for w in words[1:])]


def _walk(query: str, home: str, budget_s: float = 1.2, max_entries: int = 60000) -> list[str]:
    words = [w.lower() for w in query.split()]
    found: list[str] = []
    seen = 0
    deadline = time.monotonic() + budget_s
    stack = [(home, 0)]
    while stack:
        folder, depth = stack.pop()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    seen += 1
                    name = entry.name
                    if name.startswith("."):
                        continue
                    lower = name.lower()
                    if all(w in lower for w in words):
                        found.append(entry.path)
                    if depth < 6 and name not in SKIP_DIRS and entry.is_dir(follow_symlinks=False):
                        stack.append((entry.path, depth + 1))
        except OSError:
            continue
        if seen > max_entries or time.monotonic() > deadline or len(found) > 500:
            break
    return found


def search(query: str, limit: int = 8, home: str | None = None) -> list[str]:
    query = query.strip()
    if len(query) < 2:
        return []
    home = home or str(Path.home())
    paths = _locate(query, home)
    if not paths:  # no locate, or its database is out of date: walk instead
        paths = _walk(query, home)
    return rank_files(query, paths, limit)
