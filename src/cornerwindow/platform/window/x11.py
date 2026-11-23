"""X11: EWMH hints, focus hand-back and fullscreen detection through libwnck."""

from __future__ import annotations

from typing import Callable

from gi.repository import Gdk, GdkX11, Gtk

from ...core import log
from .base import WindowBackend

_log = log.get("x11")

try:
    import gi

    gi.require_version("Wnck", "3.0")
    from gi.repository import Wnck

    # Pager clients get their activation requests honoured by focus-stealing prevention.
    Wnck.set_client_type(Wnck.ClientType.PAGER)
except (ImportError, ValueError):
    Wnck = None


class X11Backend(WindowBackend):
    name = "x11"

    def __init__(self) -> None:
        self._screen = Wnck.Screen.get_default() if Wnck else None
        self._previous = None
        if self._screen:
            self._screen.force_update()

    def take_focus(self, window: Gtk.Window, time: int) -> None:
        if self._screen:
            active = self._screen.get_active_window()
            own = window.get_window()
            if active and own and active.get_xid() != own.get_xid():
                self._previous = active
        window.set_accept_focus(True)
        stamp = time or self._server_time(window)
        window.present_with_time(stamp)
        gdk_window = window.get_window()
        if gdk_window:
            gdk_window.focus(stamp)

    def release_focus(self, window: Gtk.Window, hand_back: bool = True) -> None:
        had_focus = window.is_active()
        window.set_accept_focus(False)
        previous, self._previous = self._previous, None
        if had_focus and hand_back and previous is not None:
            try:
                previous.activate(self._server_time(window))
            except Exception as exc:  # window may have closed meanwhile
                _log.debug("Could not hand focus back: %s", exc)

    @staticmethod
    def _server_time(window: Gtk.Window) -> int:
        gdk_window = window.get_window()
        if isinstance(gdk_window, GdkX11.X11Window):
            return GdkX11.x11_get_server_time(gdk_window)
        return Gtk.get_current_event_time()

    def watch_fullscreen(self, window: Gtk.Window, callback: Callable[[bool], None]) -> None:
        if not self._screen:
            return
        state = {"watched": None, "handler": 0, "last": None}

        def evaluate(*_args) -> None:
            active = self._screen.get_active_window()
            fullscreen = False
            if active is not None and active.is_fullscreen():
                own = window.get_window()
                if own is None or active.get_xid() != own.get_xid():
                    fullscreen = self._same_monitor(window, active)
            if fullscreen != state["last"]:
                state["last"] = fullscreen
                callback(fullscreen)

        def active_changed(screen, _previous) -> None:
            if state["watched"] is not None and state["handler"]:
                try:
                    state["watched"].disconnect(state["handler"])
                except Exception:
                    pass
            active = screen.get_active_window()
            state["watched"] = active
            state["handler"] = active.connect("state-changed", evaluate) if active else 0
            evaluate()

        self._screen.connect("active-window-changed", active_changed)
        active_changed(self._screen, None)

    @staticmethod
    def _same_monitor(window: Gtk.Window, other) -> bool:
        display = Gdk.Display.get_default()
        own = window.get_window()
        if own is None:
            return True
        x, y, w, h = other.get_geometry()
        theirs = display.get_monitor_at_point(x + w // 2, y + h // 2)
        mine = display.get_monitor_at_window(own)
        return theirs == mine
