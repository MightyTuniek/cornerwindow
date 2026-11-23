"""Emoji and typographic symbols for ':heart' in the command bar.

Emoji names come from GTK's own emoji chooser data (shipped inside libgtk), so nothing is bundled
but a short table of symbols that aren't emoji. Searching is pure and tested."""

from __future__ import annotations

from functools import lru_cache

# (character, name and keywords)
SYMBOLS: list[tuple[str, str]] = [
    ("→", "right arrow"), ("←", "left arrow"), ("↑", "up arrow"), ("↓", "down arrow"),
    ("↔", "left right arrow"), ("⇒", "double right arrow implies"), ("⇐", "double left arrow"),
    ("⇔", "double left right arrow iff"), ("↵", "return enter"), ("⌘", "command cmd"), ("⌥", "option alt"),
    ("⇧", "shift"), ("⌃", "control ctrl"), ("⌫", "backspace delete"), ("⎋", "escape esc"), ("⇥", "tab"),
    ("°", "degree"), ("±", "plus minus"), ("×", "times multiply"), ("÷", "divide division"),
    ("≈", "approximately almost equal"), ("≠", "not equal"), ("≤", "less or equal"), ("≥", "greater or equal"),
    ("∞", "infinity"), ("√", "square root"), ("∑", "sum sigma"), ("∏", "product"), ("∆", "delta increment"),
    ("π", "pi"), ("µ", "micro mu"), ("Ω", "ohm omega"), ("α", "alpha"), ("β", "beta"), ("λ", "lambda"),
    ("θ", "theta"), ("σ", "sigma"), ("²", "squared superscript two"), ("³", "cubed superscript three"),
    ("½", "half"), ("⅓", "third"), ("¼", "quarter"), ("¾", "three quarters"), ("‰", "per mille"),
    ("€", "euro"), ("£", "pound sterling"), ("¥", "yen yuan"), ("¢", "cent"), ("₹", "rupee"),
    ("₿", "bitcoin"), ("zł", "zloty"),
    ("©", "copyright"), ("®", "registered"), ("™", "trademark"), ("§", "section"), ("¶", "pilcrow paragraph"),
    ("†", "dagger"), ("•", "bullet"), ("·", "middle dot interpunct"), ("…", "ellipsis dots"),
    ("—", "em dash"), ("–", "en dash"), ("‘’", "single quotes curly"), ("“”", "double quotes curly"),
    ("„”", "low quotes polish german"), ("«»", "guillemets angle quotes"), ("‹›", "single guillemets"),
    ("✓", "check tick"), ("✗", "cross ballot x"), ("★", "star black"), ("☆", "star white"),
    ("♥", "heart suit"), ("♠", "spade"), ("♦", "diamond"), ("♣", "club"), ("♪", "music note"),
    ("☀", "sun"), ("☁", "cloud"), ("☂", "umbrella"), ("⚡", "lightning"), ("☎", "phone telephone"),
    ("✉", "envelope mail"), ("✎", "pencil"), ("✂", "scissors"), ("⌛", "hourglass"), ("⏎", "return key"),
    ("¯\\_(ツ)_/¯", "shrug"), ("(╯°□°)╯︵ ┻━┻", "table flip"), ("ಠ_ಠ", "look of disapproval"),
]

Entry = tuple[str, str, tuple[str, ...]]  # (character, name, keywords)


@lru_cache(maxsize=1)
def load() -> list[Entry]:
    """Every emoji GTK knows (empty if its data can't be read) followed by the symbols."""
    entries: list[Entry] = []
    try:
        from gi.repository import Gio, GLib

        data = Gio.resources_lookup_data("/org/gtk/libgtk/emoji/en.data", Gio.ResourceLookupFlags.NONE)
        variant = GLib.Variant.new_from_bytes(GLib.VariantType("a(ausasu)"), data, True)
        for codepoints, name, keywords, _group in variant.unpack():
            char = "".join(chr(c) for c in codepoints if c)  # 0 = where a skin tone would go
            entries.append((char, name, tuple(keywords)))
    except Exception:
        pass
    known = {e[0].rstrip("\ufe0f") for e in entries}
    entries.extend((char, name, tuple(name.split())) for char, name in SYMBOLS
                   if char.rstrip("\ufe0f") not in known)  # GTK already has ♥ ☀ ✉ …
    return entries


def _score(words: list[str], entry: Entry) -> int:
    _char, name, keywords = entry
    name_words = name.lower().replace("-", " ").split()
    total = 0
    for word in words:
        if name.lower() == word:
            total += 100
        elif name_words and name_words[0].startswith(word):
            total += 60
        elif any(w.startswith(word) for w in name_words):
            total += 40
        elif any(k.lower().startswith(word) for k in keywords):
            total += 20
        elif len(word) >= 3 and word in name.lower():
            total += 10
        else:
            return 0
    return total


def search(query: str, entries: list[Entry] | None = None, limit: int = 8) -> list[Entry]:
    words = query.lower().replace(":", " ").split()
    if not words:
        return []
    entries = load() if entries is None else entries
    scored = [(s, i, e) for i, e in enumerate(entries) if (s := _score(words, e))]
    scored.sort(key=lambda t: (-t[0], len(t[2][1]), t[1]))
    return [e for _s, _i, e in scored[:limit]]
