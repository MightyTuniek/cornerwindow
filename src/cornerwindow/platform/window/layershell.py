"""Wayland compositors with wlr-layer-shell (KDE Plasma, Hyprland, Sway, river, Wayfire...)."""

from __future__ import annotations

import gi

gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, Gtk, GtkLayerShell  # noqa: E402

from .base import WindowBackend  # noqa: E402


class LayerShellBackend(WindowBackend):
    name = "layershell"

    @staticmethod
    def supported() -> bool:
        return bool(GtkLayerShell.is_supported())

    def configure(self, window: Gtk.Window, type_hint: str = "utility") -> None:
        GtkLayerShell.init_for_window(window)
        GtkLayerShell.set_namespace(window, "cornerwindow")
        GtkLayerShell.set_layer(window, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_exclusive_zone(window, -1)
        GtkLayerShell.set_keyboard_mode(window, GtkLayerShell.KeyboardMode.NONE)
        window.set_decorated(False)

    def place(self, window: Gtk.Window, corner: str, geometry: Gdk.Rectangle,
              width: int, height: int) -> None:
        display = Gdk.Display.get_default()
        monitor = display.get_monitor_at_point(geometry.x + 1, geometry.y + 1)
        GtkLayerShell.set_monitor(window, monitor)
        for edge in GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT, GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM:
            GtkLayerShell.set_anchor(window, edge, False)
        GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.TOP if corner.startswith("top")
                                 else GtkLayerShell.Edge.BOTTOM, True)
        GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.LEFT if corner.endswith("left")
                                 else GtkLayerShell.Edge.RIGHT, True)
        window.set_size_request(width, height)

    def take_focus(self, window: Gtk.Window, time: int) -> None:
        GtkLayerShell.set_keyboard_mode(window, GtkLayerShell.KeyboardMode.ON_DEMAND)
        window.present()

    def release_focus(self, window: Gtk.Window) -> None:
        GtkLayerShell.set_keyboard_mode(window, GtkLayerShell.KeyboardMode.NONE)
