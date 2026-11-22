"""Window placement backends: how we pin a borderless, always-on-top surface to a corner.

  x11         EWMH hints (keep-above, sticky, skip taskbar) + Wnck for focus/fullscreen
  layershell  wlr-layer-shell via gtk-layer-shell (KDE, Hyprland, Sway, river, ...)

GNOME on Wayland has no layer-shell, so the app re-executes itself under
XWayland and uses the x11 backend (see platform.session.gdk_backend_override).
"""

from __future__ import annotations

from ...core import log
from .base import WindowBackend

_log = log.get("window")


def pick_backend() -> WindowBackend:
    from gi.repository import Gdk

    display = Gdk.Display.get_default()
    name = type(display).__name__
    if "Wayland" in name:
        try:
            from .layershell import LayerShellBackend

            if LayerShellBackend.supported():
                return LayerShellBackend()
        except (ImportError, ValueError) as exc:
            _log.warning("gtk-layer-shell unavailable (%s); placement may be wrong", exc)
        from .base import GenericBackend

        return GenericBackend()
    from .x11 import X11Backend

    return X11Backend()
