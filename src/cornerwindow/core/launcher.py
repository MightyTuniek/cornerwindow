"""Ranking installed apps against what you type in the command bar (pure, tested)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AppEntry:
    name: str
    keywords: list[str] = field(default_factory=list)  # Keywords, GenericName, executable
    ref: Any = None  # the Gio.AppInfo


def _subsequence(needle: str, hay: str) -> bool:
    it = iter(hay)
    return all(ch in it for ch in needle)


def score(query: str, entry: AppEntry) -> float:
    """Higher is better; 0 = no match."""
    q = query.strip().lower()
    if not q:
        return 0.0
    name = entry.name.lower()
    if name == q:
        return 120.0
    if name.startswith(q):
        return 100.0 - len(name) / 100
    words = name.replace("-", " ").replace("_", " ").split()
    if any(w.startswith(q) for w in words):
        return 80.0 - len(name) / 100
    if q in name:
        return 60.0
    if any(k.lower().startswith(q) for k in entry.keywords if k):
        return 50.0
    if len(q) >= 3 and _subsequence(q, name):
        return 20.0
    return 0.0


def rank_apps(query: str, entries: list[AppEntry], limit: int = 5) -> list[AppEntry]:
    scored = [(score(query, e), e) for e in entries]
    scored = [(s, e) for s, e in scored if s > 0]
    scored.sort(key=lambda se: (-se[0], se[1].name.lower()))
    return [e for _s, e in scored[:limit]]
