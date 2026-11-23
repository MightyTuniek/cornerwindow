"""Editing the user's TOML file without destroying it.

Python can read TOML (tomllib) but not write it, and a naive dump would throw
away the user's comments and layout. `TomlDocument` edits the text in place:
it replaces just the value of `key = ...` inside the right `[table]`, keeping
comments, ordering and spacing; missing tables/keys are appended cleanly;
`[[array.of.tables]]` blocks are rewritten as a unit.

`UserConfigWriter` is what the settings window uses: it writes only values
that differ from the default/machine layers and deletes keys that are set
back to their default, so ~/.config/cornerwindow/config.toml stays minimal.
"""

from __future__ import annotations

import hashlib
import math
import re
import tomllib
from pathlib import Path
from typing import Any

from .core import log

_log = log.get("config-edit")
_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
_TABLE = re.compile(r"^\s*\[\s*([^\[\]]+?)\s*\]\s*(#.*)?$")
_ARRAY_TABLE = re.compile(r"^\s*\[\[\s*([^\[\]]+?)\s*\]\]\s*(#.*)?$")
_KEY = re.compile(r'^(\s*)("(?:[^"\\]|\\.)*"|[A-Za-z0-9_-]+)\s*=\s*')


# ── serialisation ─────────────────────────────────────────────────────────────
def dump_key(key: str) -> str:
    return key if _BARE_KEY.match(key) else dump_value(key)


def dump_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError("TOML cannot store NaN/inf here")
        text = repr(round(value, 6))
        return text if any(c in text for c in ".e") else text + ".0"
    if isinstance(value, str):
        escaped = (value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
                   .replace("\t", "\\t"))
        return f'"{escaped}"'
    if isinstance(value, (list, tuple)):
        items = [dump_value(v) for v in value]
        if value and all(isinstance(v, (list, tuple)) for v in value):
            return "[\n" + "".join(f"  {item},\n" for item in items) + "]"
        return "[" + ", ".join(items) + "]"
    if isinstance(value, dict):
        if not value:
            return "{}"
        return "{ " + ", ".join(f"{dump_key(k)} = {dump_value(v)}" for k, v in value.items()) + " }"
    raise TypeError(f"cannot write {type(value).__name__} to TOML")


def _split_dotted(header: str) -> str:
    return ".".join(part.strip().strip('"') for part in header.split("."))


# ── in-place document editing ────────────────────────────────────────────────
class TomlDocument:
    def __init__(self, text: str = "") -> None:
        self.lines = text.splitlines()

    def text(self) -> str:
        out = "\n".join(self.lines).rstrip("\n")
        return out + "\n" if out else ""

    # -- structure ----------------------------------------------------------------------
    def _headers(self) -> list[tuple[int, str, bool]]:
        """(line index, table name, is_array_table) for every header."""
        found = []
        in_multiline = False
        for i, line in enumerate(self.lines):
            if in_multiline:
                if line.count('"""') % 2 == 1 or line.count("'''") % 2 == 1:
                    in_multiline = False
                continue
            if line.count('"""') % 2 == 1 or line.count("'''") % 2 == 1:
                in_multiline = True
                continue
            m = _ARRAY_TABLE.match(line)
            if m:
                found.append((i, _split_dotted(m.group(1)), True))
                continue
            m = _TABLE.match(line)
            if m and not line.lstrip().startswith("[["):
                found.append((i, _split_dotted(m.group(1)), False))
        return found

    def _table_span(self, table: str) -> tuple[int, int] | None:
        """[start, end) line range of a [table]'s body ('' = top level before any header)."""
        headers = self._headers()
        if table == "":
            return (0, headers[0][0] if headers else len(self.lines))
        for n, (idx, name, is_array) in enumerate(headers):
            if name == table and not is_array:
                end = headers[n + 1][0] if n + 1 < len(headers) else len(self.lines)
                return (idx + 1, end)
        return None

    def _value_extent(self, line_no: int, col: int) -> tuple[int, int | None]:
        """From (line, col) where a value starts, find where it ends.

        Returns (last line index of the value, column of a trailing # comment or None)."""
        depth = 0
        quote: str | None = None
        i, j = line_no, col
        while i < len(self.lines):
            line = self.lines[i]
            while j < len(line):
                ch = line[j]
                if quote in ('"""', "'''"):
                    if line.startswith(quote, j):
                        quote = None
                        j += 3
                    else:
                        j += 1
                    continue
                if quote == '"':
                    if ch == "\\":
                        j += 2
                        continue
                    if ch == '"':
                        quote = None
                    j += 1
                    continue
                if quote == "'":
                    if ch == "'":
                        quote = None
                    j += 1
                    continue
                if line.startswith('"""', j) or line.startswith("'''", j):
                    quote = line[j:j + 3]
                    j += 3
                    continue
                if ch in "\"'":
                    quote = ch
                elif ch in "[{":
                    depth += 1
                elif ch in "]}":
                    depth -= 1
                elif ch == "#" and depth <= 0:
                    return i, j
                j += 1
            if depth <= 0 and quote is None:
                return i, None
            i += 1
            j = 0
        return max(len(self.lines) - 1, line_no), None

    def _find_key(self, table: str, key: str) -> tuple[int, int, int, int | None] | None:
        """(line, value start col, value end line, trailing comment column) of key in table."""
        span = self._table_span(table)
        if span is None:
            return None
        i = span[0]
        while i < span[1]:
            m = _KEY.match(self.lines[i])
            if m and m.group(2).strip('"') == key:
                end_line, comment_col = self._value_extent(i, m.end())
                return i, m.end(), end_line, comment_col
            if m:  # skip over multi-line values of other keys
                i = self._value_extent(i, m.end())[0]
            i += 1
        return None

    # -- edits --------------------------------------------------------------------------
    def set(self, table: str, key: str, value: Any) -> None:
        rendered = dump_value(value)
        found = self._find_key(table, key)
        if found:
            line, col, end_line, comment_col = found
            prefix = self.lines[line][:col]
            comment = self.lines[end_line][comment_col:] if comment_col is not None else ""
            text = prefix + rendered
            if comment:  # keep the comment where it was, as long as the value fits
                last = len(text.split("\n")[-1])
                target = comment_col if end_line == line else last + 2
                text += " " * max(target - last, 2) + comment
            self.lines[line:end_line + 1] = text.split("\n")
            return
        span = self._table_span(table)
        entry = f"{dump_key(key)} = {rendered}".split("\n")
        if span is None:
            while self.lines and not self.lines[-1].strip():
                self.lines.pop()
            if self.lines:
                self.lines.append("")
            self.lines.append(f"[{table}]")
            self.lines.extend(entry)
            return
        # insert after the last non-blank line of the table body
        insert_at = span[1]
        while insert_at > span[0] and not self.lines[insert_at - 1].strip():
            insert_at -= 1
        self.lines[insert_at:insert_at] = entry

    def delete(self, table: str, key: str) -> bool:
        found = self._find_key(table, key)
        if not found:
            return False
        line, _col, end_line, _comment = found
        del self.lines[line:end_line + 1]
        return True

    def set_array_of_tables(self, name: str, items: list[dict]) -> None:
        """Replace every [[name]] block with `items` (empty list removes them)."""
        headers = self._headers()
        blocks = []
        for n, (idx, hname, is_array) in enumerate(headers):
            if hname == name and is_array:
                end = headers[n + 1][0] if n + 1 < len(headers) else len(self.lines)
                blocks.append((idx, end))
        insert_at = blocks[0][0] if blocks else None
        for start, end in reversed(blocks):
            del self.lines[start:end]
        rendered: list[str] = []
        for item in items:
            rendered.append(f"[[{name}]]")
            rendered.extend(f"{dump_key(k)} = {dump_value(v)}" for k, v in item.items())
            rendered.append("")
        if insert_at is None:
            while self.lines and not self.lines[-1].strip():
                self.lines.pop()
            if self.lines and rendered:
                self.lines.append("")
            self.lines.extend(rendered)
        else:
            self.lines[insert_at:insert_at] = rendered

    def remove_empty_tables(self) -> None:
        headers = self._headers()
        for n in range(len(headers) - 1, -1, -1):
            idx, _name, is_array = headers[n]
            if is_array:
                continue
            end = headers[n + 1][0] if n + 1 < len(headers) else len(self.lines)
            body = [ln for ln in self.lines[idx + 1:end] if ln.strip() and not ln.strip().startswith("#")]
            if not body:
                del self.lines[idx:end]


def _escaped(line: str, j: int) -> bool:
    backslashes = 0
    k = j - 1
    while k >= 0 and line[k] == "\\":
        backslashes += 1
        k -= 1
    return backslashes % 2 == 1


# ── the settings window's writer ─────────────────────────────────────────────
HEADER = """\
# CornerWindow — your settings. Edit here or in the island's settings window (⚙);
# both stay in sync. Only values that differ from the defaults/machine file live here.
"""

ARRAY_TABLES = {("display", "presets")}  # written as [[display.presets]] blocks


def file_hash(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


class UserConfigWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.last_written_hash: str | None = None

    def read_text(self) -> str:
        try:
            return self.path.read_text()
        except FileNotFoundError:
            return HEADER

    def write_text(self, text: str) -> None:
        tomllib.loads(text)  # never write a broken file
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(text)
        tmp.replace(self.path)
        self.last_written_hash = file_hash(text)

    def set(self, section: str, key: str, value: Any, base_value: Any = None) -> None:
        """Write section.key = value, or remove it when it equals the lower layers."""
        self.set_many(section, {key: value}, {key: base_value})

    def set_many(self, section: str, values: dict[str, Any], base_values: dict[str, Any] | None = None) -> None:
        """Several keys of one section in a single write (e.g. the whole card layout)."""
        base_values = base_values or {}
        doc = TomlDocument(self.read_text())
        for key, value in values.items():
            same_as_base = _equal(value, base_values.get(key))
            if (section, key) in ARRAY_TABLES:
                doc.set_array_of_tables(f"{section}.{key}", [] if same_as_base else list(value))
            elif same_as_base:
                doc.delete(section, key)
            else:
                doc.set(section, key, value)
        doc.remove_empty_tables()
        self.write_text(doc.text())

    def reset(self, section: str, *keys: str) -> None:
        doc = TomlDocument(self.read_text())
        for key in keys:
            if (section, key) in ARRAY_TABLES:
                doc.set_array_of_tables(f"{section}.{key}", [])
            else:
                doc.delete(section, key)
        doc.remove_empty_tables()
        self.write_text(doc.text())


def _equal(a: Any, b: Any) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        try:
            return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9)
        except (TypeError, ValueError):
            return False
    return a == b
