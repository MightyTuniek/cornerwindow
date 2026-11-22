"""CornerWindow: a hidden quarter-circle in a screen corner that morphs into a quick panel."""

__version__ = "0.1.0"
APP_ID = "io.github.cornerwindow"

try:  # pin GTK 3 before anything imports gi.repository.Gtk
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("GdkX11", "3.0")
except (ImportError, ValueError):
    pass
