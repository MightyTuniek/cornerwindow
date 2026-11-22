"""What kind of desktop are we running on?"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class SessionInfo:
    type: str  # "x11" | "wayland" | "unknown"
    desktop: str  # lower-cased XDG_CURRENT_DESKTOP, e.g. "zorin:gnome", "kde", "hyprland"

    @property
    def is_gnome(self) -> bool:
        return "gnome" in self.desktop or "unity" in self.desktop or "zorin" in self.desktop

    @property
    def is_kde(self) -> bool:
        return "kde" in self.desktop or "plasma" in self.desktop

    @property
    def is_wayland(self) -> bool:
        return self.type == "wayland"


def detect() -> SessionInfo:
    stype = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if not stype:
        if os.environ.get("WAYLAND_DISPLAY"):
            stype = "wayland"
        elif os.environ.get("DISPLAY"):
            stype = "x11"
        else:
            stype = "unknown"
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("DESKTOP_SESSION") or "").lower()
    return SessionInfo(stype, desktop)


def gdk_backend_override(info: SessionInfo) -> str | None:
    """GNOME on Wayland has no layer-shell; run under XWayland so we can position ourselves."""
    if info.is_wayland and info.is_gnome and "GDK_BACKEND" not in os.environ:
        return "x11"
    return None
