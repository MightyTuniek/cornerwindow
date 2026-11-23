"""Text snippets: a short name expands to text, with placeholders filled in at use.

    {date} 2026-11-23   {time} 14:05   {datetime} 2026-11-23 14:05   {weekday} Monday
    {clipboard} what's on the clipboard   {date:%d.%m.%Y} any strftime format   {{ }} literal braces
"""

from __future__ import annotations

import datetime as dt
import re

_FIELD = re.compile(r"\{\{|\}\}|\{([a-z]+)(?::([^}]*))?\}")


def expand(text: str, now: dt.datetime | None = None, clipboard: str = "") -> str:
    now = now or dt.datetime.now()

    def field(match: re.Match) -> str:
        whole = match.group(0)
        if whole == "{{":
            return "{"
        if whole == "}}":
            return "}"
        name, fmt = match.group(1), match.group(2)
        if fmt and name in ("date", "time", "datetime"):
            return now.strftime(fmt)
        values = {
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M"),
            "datetime": now.strftime("%Y-%m-%d %H:%M"),
            "weekday": now.strftime("%A"),
            "clipboard": clipboard,
        }
        return values.get(name, whole)

    return _FIELD.sub(field, text)


def needs_clipboard(text: str) -> bool:
    return "{clipboard}" in text


def matching(snippets: dict[str, str], query: str) -> list[tuple[str, str]]:
    """Name prefix matches first, then names or texts containing the query."""
    query = query.strip().lower()
    items = sorted(snippets.items())
    if not query:
        return items
    first = [(k, v) for k, v in items if k.lower().startswith(query)]
    rest = [(k, v) for k, v in items if (k, v) not in first and (query in k.lower() or query in v.lower())]
    return first + rest
