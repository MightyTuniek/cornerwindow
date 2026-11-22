from __future__ import annotations

from typing import Callable

from gi.repository import Gdk, Gtk


class WindowBackend:
    """Default implementation: plain GTK calls that work 'well enough' anywhere."""

    name = "generic"

    def configure(self, window: Gtk.Window, type_hint: str = "utility") -> None:
        window.set_decorated(False)
        window.set_resizable(False)
        window.set_skip_taskbar_hint(True)
        window.set_skip_pager_hint(True)
        window.set_keep_above(True)
        window.stick()
        window.set_accept_focus(False)
        window.set_focus_on_map(False)
        hint = Gdk.WindowTypeHint.DOCK if type_hint == "dock" else Gdk.WindowTypeHint.UTILITY
        window.set_type_hint(hint)

    def place(self, window: Gtk.Window, corner: str, geometry: Gdk.Rectangle,
              width: int, height: int) -> None:
        x = geometry.x if corner.endswith("left") else geometry.x + geometry.width - width
        y = geometry.y if corner.startswith("top") else geometry.y + geometry.height - height
        window.set_size_request(width, height)
        window.resize(width, height)
        window.move(x, y)

    def take_focus(self, window: Gtk.Window, time: int) -> None:
        window.set_accept_focus(True)
        window.present_with_time(time or Gtk.get_current_event_time())

    def release_focus(self, window: Gtk.Window) -> None:
        window.set_accept_focus(False)

    def raise_above(self, window: Gtk.Window) -> None:
        """Put the window back above everything, fullscreen apps included."""
        window.set_keep_above(True)
        gdk_window = window.get_window()
        if gdk_window:
            gdk_window.raise_()

    def watch_fullscreen(self, window: Gtk.Window, callback: Callable[[bool], None]) -> None:
        """Call callback(True) while a fullscreen app is focused on our monitor."""


GenericBackend = WindowBackend
