"""Recent clipboard text and images; click an entry to copy it again, pin the ones you reuse."""

from __future__ import annotations

from gi.repository import Gtk, Pango

from ..core.activity import GREEN
from ..ui.widgets import IconButton, label
from .base import Command, Module, Suggestion, module


@module("clipboard", title="Clipboard", area="drawer", icon="copy",
        description="Recent clipboard text and images, pins, paste as plain text")
class Clipboard(Module):
    def setup(self) -> None:
        self.history = self.ctx.services.clipboard
        self.history.on_change(self._render)

    def configure(self) -> None:
        self.history.size = int(self.config.get("size", 10))
        self.history.persist = bool(self.config.get("persist", False))
        self.history.image_count = int(self.config.get("images", 4))
        self.history.clear_after = float(self.config.get("clear_after", 0))
        self.ctx.actions.register("clipboard.plain", self._plain, "Paste as plain text (clipboard without formatting)")

    def build(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        head = Gtk.Box()
        self.hint = label("Copied text shows up here", "cw-dim cw-small")
        head.pack_start(self.hint, True, True, 0)
        head.pack_end(IconButton("clear", self.history.clear, "Clear history", "cw-icon cw-plain", 14),
                      False, False, 0)
        outer.pack_start(head, False, False, 0)
        self.image_row = Gtk.Box(spacing=6)
        self.image_row.set_no_show_all(True)
        outer.pack_start(self.image_row, False, False, 0)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        scroller.add(self.list)
        outer.pack_start(scroller, True, True, 0)
        self._render()
        return outer

    def _render(self) -> None:
        if not hasattr(self, "list"):
            return
        for child in self.list.get_children():
            self.list.remove(child)
            child.destroy()
        for child in self.image_row.get_children():
            self.image_row.remove(child)
            child.destroy()
        for pixbuf in self.history.images:
            self.image_row.pack_start(self._image_button(pixbuf), False, False, 0)
        self.image_row.set_visible(bool(self.history.images))
        for child in self.image_row.get_children():
            child.show_all()
        for text in self.history.pinned + self.history.unpinned():
            self.list.pack_start(self._text_row(text), False, False, 0)
        count = len(self.history.items)
        self.hint.set_text(f"{count} recent" + (f" · {len(self.history.pinned)} pinned" if self.history.pinned else "")
                           if count or self.history.pinned else "Copied text shows up here")
        self.list.show_all()

    def _text_row(self, text: str) -> Gtk.Widget:
        line = Gtk.Box(spacing=2)
        button = Gtk.Button()
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_can_focus(False)
        button.get_style_context().add_class("cw-row")
        row = Gtk.Label(label=" ".join(text.split())[:300])
        row.set_xalign(0)
        row.set_ellipsize(Pango.EllipsizeMode.END)
        row.set_max_width_chars(1)
        button.add(row)
        button.set_tooltip_text(text[:500])
        button.connect("clicked", lambda _b, t=text: self._copy(t))
        line.pack_start(button, True, True, 0)
        pinned = self.history.is_pinned(text)
        pin = IconButton("pin", lambda t=text, on=not pinned: self.history.pin(t, on),
                         "Unpin" if pinned else "Pin: keep it here", "cw-icon cw-plain cw-mini", 11)
        if pinned:
            pin.get_style_context().add_class("cw-pinned")
        else:
            pin.set_opacity(0.45)
        line.pack_start(pin, False, False, 0)
        return line

    def _image_button(self, pixbuf) -> Gtk.Widget:
        from gi.repository import GdkPixbuf

        height = 40
        width = max(1, round(pixbuf.get_width() * height / max(pixbuf.get_height(), 1)))
        thumb = pixbuf.scale_simple(min(width, 96), height, GdkPixbuf.InterpType.BILINEAR)
        button = Gtk.Button()
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_can_focus(False)
        button.get_style_context().add_class("cw-thumb")
        button.add(Gtk.Image.new_from_pixbuf(thumb))
        button.set_tooltip_text(f"Image {pixbuf.get_width()}×{pixbuf.get_height()} · click to copy")
        button.connect("clicked", lambda _b, p=pixbuf: self._copy_image(p))
        return button

    def _copy_image(self, pixbuf) -> None:
        self.history.copy_image(pixbuf)
        self.ctx.activity.show("clipboard", dot=True, color=GREEN, timeout=0.9)
        self.ctx.island.close()

    def _plain(self) -> str:
        text = self.history.plain()
        if text is None:
            return "Nothing to paste"
        self.ctx.activity.show("clipboard", dot=True, color=GREEN, timeout=0.9)
        return "The clipboard is plain text now"

    def _copy(self, text: str) -> None:
        self.history.copy(text)
        self.ctx.activity.show("clipboard", dot=True, color=GREEN, timeout=0.9)
        self.ctx.island.close()

    def commands(self) -> list[Command]:
        def matching(arg: str) -> list[str]:
            words = arg.lower().split()
            pool = self.history.pinned + self.history.unpinned()
            return [t for t in pool if all(w in t.lower() for w in words)]

        def suggest(arg: str) -> list[Suggestion]:
            items = matching(arg)
            if not items:
                return [Suggestion("Nothing in the clipboard history matches" if arg.strip()
                                   else "The clipboard history is empty", "", lambda: None, "copy")]
            return [Suggestion(" ".join(t.split())[:120], "Copy", lambda t=t: self._copy_from_bar(t), "copy")
                    for t in items[:8]]

        def run(arg: str) -> str:
            items = matching(arg)
            return self._copy_from_bar(items[0]) if items else "No match"

        return [Command(("cb", "clipboard"), "Search clipboard history", "cb · cb password", run, True,
                        suggest=suggest),
                Command(("plain",), "Paste as plain text (drop the formatting)", "plain", lambda _a: self._plain())]

    def _copy_from_bar(self, text: str) -> str:
        self.history.copy(text)
        self.ctx.activity.show("clipboard", dot=True, color=GREEN, timeout=0.9)
        return "Copied"
