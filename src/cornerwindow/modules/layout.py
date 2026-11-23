"""The card layout as data: where every module sits and which ones are switched off.

Pure logic (no GTK), so the settings Layout page and the tests share it. The page edits a
`Layout`, then writes `top`, `columns`, `drawer`, `tools` and `hidden` back to `[modules]`.

A destination is "top", "drawer", "tools", "pool" (not in the layout) or ("col", index).
Tools are modules without a card (area = "tool"): they only add command-bar entries and actions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

Dest = Union[str, tuple]


@dataclass
class Layout:
    top: list[str] = field(default_factory=list)
    columns: list[list[str]] = field(default_factory=lambda: [[]])
    drawer: list[str] = field(default_factory=list)
    hidden: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)

    # -- conversion -----------------------------------------------------------------
    @classmethod
    def from_config(cls, section: dict) -> "Layout":
        columns = [list(c) for c in (section.get("columns") or []) if isinstance(c, list)]
        return cls(
            top=list(section.get("top", ["commandbar"]) or []),
            columns=columns or [[]],
            drawer=list(section.get("drawer", []) or []),
            hidden=list(section.get("hidden", []) or []),
            tools=list(section.get("tools", []) or []),
        )

    def to_config(self) -> dict:
        columns = [list(c) for c in self.columns if c] or [[]]
        placed = set(self.placed())
        return {
            "top": list(self.top),
            "columns": columns,
            "drawer": list(self.drawer),
            "tools": list(self.tools),
            "hidden": [m for m in dict.fromkeys(self.hidden) if m in placed],
        }

    def copy(self) -> "Layout":
        return Layout(list(self.top), [list(c) for c in self.columns], list(self.drawer), list(self.hidden),
                      list(self.tools))

    # -- queries --------------------------------------------------------------------
    def placed(self) -> list[str]:
        out = list(self.top)
        for column in self.columns:
            out.extend(column)
        out.extend(self.drawer)
        out.extend(self.tools)
        return out

    def where(self, mid: str) -> Dest:
        if mid in self.top:
            return "top"
        if mid in self.drawer:
            return "drawer"
        if mid in self.tools:
            return "tools"
        for i, column in enumerate(self.columns):
            if mid in column:
                return ("col", i)
        return "pool"

    def visible(self, mid: str) -> bool:
        return self.where(mid) != "pool" and mid not in self.hidden

    def unplaced(self, known: list[str]) -> list[str]:
        placed = set(self.placed())
        return [m for m in known if m not in placed]

    def shown_ids(self) -> list[str]:
        """Every module that ends up built (placed and not hidden)."""
        return [m for m in self.placed() if m not in self.hidden]

    # -- edits ----------------------------------------------------------------------
    def _list(self, dest: Dest) -> list[str] | None:
        if dest == "top":
            return self.top
        if dest == "drawer":
            return self.drawer
        if dest == "tools":
            return self.tools
        if isinstance(dest, tuple) and dest[0] == "col":
            while len(self.columns) <= dest[1]:
                self.columns.append([])
            return self.columns[dest[1]]
        return None

    def remove(self, mid: str) -> None:
        for lst in (self.top, self.drawer, self.tools, *self.columns):
            while mid in lst:
                lst.remove(mid)

    def move(self, mid: str, dest: Dest, index: int | None = None, area: str = "main") -> bool:
        """Put `mid` at `index` of `dest` (end when None). False when the area rule forbids it."""
        if not allowed(area, dest):
            return False
        old = self.where(mid)
        old_index = None
        if old != "pool":
            old_index = self._list(old).index(mid)
        self.remove(mid)
        target = self._list(dest)
        if target is None:  # "pool": out of the layout
            self.hidden = [m for m in self.hidden if m != mid]
            return True
        if index is not None and old == dest and old_index is not None and old_index < index:
            index -= 1  # `index` counted `mid` itself, which is no longer in front of it
        if index is None or index > len(target):
            index = len(target)
        target.insert(max(0, index), mid)
        return True

    def set_visible(self, mid: str, on: bool, area: str = "main") -> None:
        if on:
            self.hidden = [m for m in self.hidden if m != mid]
            if self.where(mid) == "pool":
                if area == "drawer":
                    self.drawer.append(mid)
                elif area == "tool":
                    self.tools.append(mid)
                else:
                    self.shortest_column().append(mid)
        elif self.where(mid) != "pool" and mid not in self.hidden:
            self.hidden.append(mid)

    def shortest_column(self) -> list[str]:
        if not self.columns:
            self.columns.append([])
        return min(self.columns, key=len)

    def add_column(self) -> None:
        self.columns.append([])

    def remove_column(self, index: int) -> None:
        """Remove a column; its cards go to the column on its left (or right)."""
        if len(self.columns) <= 1 or not 0 <= index < len(self.columns):
            return
        cards = self.columns.pop(index)
        self.columns[max(0, index - 1)].extend(cards)


def allowed(area: str, dest: Dest) -> bool:
    """Drawer modules live only in the drawer, tools only among the tools; cards in neither."""
    if dest == "pool":
        return True
    if area == "drawer":
        return dest == "drawer"
    if area == "tool":
        return dest == "tools"
    return dest not in ("drawer", "tools")


# One click on the Features page: a small panel, or everything this machine can show.
PRESETS: dict[str, dict] = {
    "essentials": {
        "top": ["commandbar", "windows"],
        "columns": [["display", "audio", "media", "drives", "downloads"],
                    ["header", "toggles", "notifications", "battery", "drawer"]],
        "drawer": ["timer", "stopwatch", "clipboard", "notes"],
        "tools": ["capture", "windowtools", "snippets", "processes"],
        "hidden": [],
    },
    "everything": {
        "top": ["commandbar", "windows"],
        "columns": [["display", "audio", "media", "drives", "glance", "downloads", "updates", "agents"],
                    ["header", "toggles", "notifications", "battery", "weather", "drawer", "power"]],
        "drawer": ["timer", "stopwatch", "clipboard", "notes", "shelf"],
        "tools": ["capture", "windowtools", "snippets", "processes", "alerts"],
        "hidden": [],
    },
}


def preset(name: str) -> Layout:
    return Layout.from_config(PRESETS[name])
