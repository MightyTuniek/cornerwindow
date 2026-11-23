"""What you ran from the command bar: recents for the empty bar and ↑, and a frecency bonus
so the apps you open most float to the top (pure apart from reading/writing one JSON file)."""

from __future__ import annotations

import json
import time
from pathlib import Path

MAX_ENTRIES = 60
MAX_BONUS = 30.0


class History:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.entries: list[dict] = []  # newest first: {"line", "key", "title"}
        self.picks: dict[str, list[float]] = {}  # key -> [count, last used (epoch s)]
        self._load()

    def _load(self) -> None:
        if self.path is None:
            return
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            self.entries = [e for e in data.get("entries", []) if isinstance(e, dict) and e.get("line")]
            self.picks = {k: v for k, v in data.get("picks", {}).items()
                          if isinstance(v, list) and len(v) == 2}

    def data(self) -> dict:
        return {"entries": self.entries, "picks": self.picks}

    def save(self) -> None:
        if self.path is None:
            return
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data(), ensure_ascii=False))
            tmp.replace(self.path)
        except OSError:
            pass

    def record(self, line: str, key: str = "", title: str = "", now: float | None = None) -> None:
        line = line.strip()
        if not line:
            return
        now = time.time() if now is None else now
        ident = key or f"line:{line}"
        self.entries = [e for e in self.entries if (e.get("key") or f"line:{e['line']}") != ident]
        self.entries.insert(0, {"line": line, "key": key, "title": title or line})
        del self.entries[MAX_ENTRIES:]
        if key:
            count, _last = self.picks.get(key, [0, now])
            self.picks[key] = [count + 1, now]
            if len(self.picks) > 300:  # forget the least used
                for stale in sorted(self.picks, key=lambda k: self.picks[k][0])[:50]:
                    del self.picks[stale]

    def bonus(self, key: str, now: float | None = None) -> float:
        """0..MAX_BONUS: more for things used often and recently."""
        if key not in self.picks:
            return 0.0
        now = time.time() if now is None else now
        count, last = self.picks[key]
        age_days = max(now - last, 0) / 86400
        return min(MAX_BONUS, 4.0 * min(count, 15) / (1 + age_days / 14))

    def lines(self) -> list[str]:
        """Distinct typed lines, newest first (for ↑ in the bar)."""
        seen, out = set(), []
        for entry in self.entries:
            if entry["line"] not in seen:
                seen.add(entry["line"])
                out.append(entry["line"])
        return out

    def recent(self, limit: int = 5) -> list[dict]:
        return self.entries[:limit]

    def clear(self) -> None:
        self.entries, self.picks = [], {}
