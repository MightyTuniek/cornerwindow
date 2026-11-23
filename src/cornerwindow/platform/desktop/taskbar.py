"""Hide the desktop's own taskbar while CornerWindow runs (experimental: `[experimental] hide_taskbar`).

Backends, each probing whether it applies here:

  IntellihidePanel  Zorin Taskbar and Dash to Panel (one code base). Their auto-hide is switched on
                    with every reveal trigger off (pointer, overlapping windows, notifications), so the
                    panel stays out of sight and maximised windows get its space. The Activities
                    overview and the extension's own Super+I shortcut still bring it up.
  DockExtension     Dash to Dock, Ubuntu Dock, Zorin Dash: the extension is switched off, and on again
                    to restore.

Before anything changes, the original values are written to ~/.local/share/cornerwindow/taskbar.json.
That file is read fresh every time (not cached like State), so `cornerwindow taskbar show` from a
terminal restores exactly what was there, also after a crash. The app restores the taskbar when the
setting goes off and when it quits, and hides it again on the next start.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from ...core import log, paths

_log = log.get("taskbar")

# The values that keep a Dash-to-Panel-style panel hidden. Keys a version lacks are skipped.
INTELLIHIDE: dict[str, Any] = {
    "intellihide": True,
    "intellihide-hide-from-windows": False,
    "intellihide-hide-from-monitor-windows": False,
    "intellihide-use-pointer": False,
    "intellihide-show-on-notification": False,
    "intellihide-show-in-fullscreen": False,
    "intellihide-only-secondary": False,
    "intellihide-persisted-state": -1,
}

# (extension uuid, settings schema, name), in order of preference
PANELS = (
    ("zorin-taskbar@zorinos.com", "org.gnome.shell.extensions.zorin-taskbar", "Zorin Taskbar"),
    ("dash-to-panel@jderose9.github.com", "org.gnome.shell.extensions.dash-to-panel", "Dash to Panel"),
)
DOCKS = (
    ("ubuntu-dock@ubuntu.com", "Ubuntu Dock"),
    ("dash-to-dock@micxgx.gmail.com", "Dash to Dock"),
    ("zorin-dash@zorinos.com", "Zorin Dash"),
)


class SavedState:
    """What we changed, so it can be put back. One small JSON file, never cached."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.data_dir() / "taskbar.json"

    def load(self) -> dict | None:
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            _log.warning("Could not read %s: %s", self.path, exc)
            return None
        return data if isinstance(data, dict) and data.get("uuid") else None

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self.path)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


class _GSettings:
    """Gio.Settings reduced to what the panel backend needs (and what the tests fake)."""

    def __init__(self, settings) -> None:
        self._s = settings
        self._keys = set(settings.props.settings_schema.list_keys())

    def has(self, key: str) -> bool:
        return key in self._keys

    def get(self, key: str) -> Any:
        return self._s.get_value(key).unpack()

    def set(self, key: str, value: Any) -> None:
        from gi.repository import GLib

        kind = self._s.get_value(key).get_type_string()
        self._s.set_value(key, GLib.Variant(kind, value))


class IntellihidePanel:
    kind = "intellihide"

    def __init__(self, uuid: str, schema: str, name: str, settings) -> None:
        self.uuid, self.schema, self.name = uuid, schema, name
        self.settings = settings  # has / get / set

    def hide(self, store: SavedState) -> None:
        saved = store.load()
        if not (saved and saved.get("uuid") == self.uuid):
            # first hide (not a re-apply after a crash): remember the user's own values
            values = {k: self.settings.get(k) for k in INTELLIHIDE if self.settings.has(k)}
            store.save({"kind": self.kind, "uuid": self.uuid, "schema": self.schema, "values": values})
        for key, value in INTELLIHIDE.items():
            if self.settings.has(key) and self.settings.get(key) != value:
                self.settings.set(key, value)

    @staticmethod
    def restore(saved: dict, settings) -> None:
        for key, value in (saved.get("values") or {}).items():
            if settings.has(key) and settings.get(key) != value:
                settings.set(key, value)


class DockExtension:
    kind = "extension"

    def __init__(self, uuid: str, name: str) -> None:
        self.uuid, self.name = uuid, name

    def hide(self, store: SavedState) -> None:
        if (store.load() or {}).get("uuid") != self.uuid:
            store.save({"kind": self.kind, "uuid": self.uuid})
        _extension_call("DisableExtension", self.uuid)

    @staticmethod
    def restore(saved: dict) -> None:
        _extension_call("EnableExtension", saved["uuid"])


class Taskbar:
    """The service: which taskbar is here, and hiding / restoring it."""

    def __init__(self, store: SavedState | None = None) -> None:
        self.store = store or SavedState()

    def detect(self):
        """The backend for the taskbar running right now, or None."""
        for uuid, schema, name in PANELS:
            if _extension_active(uuid):
                settings = _panel_settings(uuid, schema)
                if settings is not None:
                    return IntellihidePanel(uuid, schema, name, settings)
        for uuid, name in DOCKS:
            if _extension_active(uuid):
                return DockExtension(uuid, name)
        return None

    def describe(self) -> str:
        """For `doctor` and the CLI: what was found and whether it is hidden."""
        saved = self.store.load()
        if saved:
            return f"hidden by CornerWindow ({_name_of(saved['uuid'])})"
        backend = self.detect()
        return f"{backend.name} (can be hidden)" if backend else "no supported taskbar found"

    def is_hidden(self) -> bool:
        return self.store.load() is not None

    def hide(self) -> str | None:
        """Hide the taskbar. Returns its name, or None when there is nothing we know how to hide."""
        backend = self.detect()
        saved = self.store.load()
        if saved and (backend is None or saved.get("uuid") != backend.uuid):
            # the hidden taskbar is not the one running now (switched extensions meanwhile)
            if backend is None and saved.get("kind") == DockExtension.kind:
                return _name_of(saved["uuid"])  # we disabled it ourselves; nothing else to do
            self.show()
        if backend is None:
            return None
        try:
            backend.hide(self.store)
        except Exception:
            _log.exception("Could not hide %s", backend.name)
            return None
        _flush()
        _log.info("Taskbar hidden (%s)", backend.name)
        return backend.name

    def show(self) -> bool:
        """Put the taskbar back as it was. False when we hadn't hidden it."""
        saved = self.store.load()
        if not saved:
            return False
        try:
            if saved.get("kind") == DockExtension.kind:
                DockExtension.restore(saved)
            else:
                settings = _panel_settings(saved["uuid"], saved.get("schema", ""))
                if settings is None:
                    _log.warning("Settings for %s are gone; nothing to restore", saved["uuid"])
                else:
                    IntellihidePanel.restore(saved, settings)
        except Exception:
            _log.exception("Could not restore the taskbar")
            return False
        _flush()
        self.store.clear()
        _log.info("Taskbar restored (%s)", _name_of(saved["uuid"]))
        return True


def command(taskbar, args: list[str], lock=None) -> tuple[str, int]:
    """`cornerwindow taskbar show|hide|status` (also run without an instance, see __main__)."""
    verb = args[0] if args else "status"
    if verb not in ("show", "hide", "status"):
        return "usage: cornerwindow taskbar show|hide|status", 2
    if lock is None:
        lock = threading.Lock()
    with lock:
        if verb == "show":
            return ("Taskbar restored" if taskbar.show() else "The taskbar wasn't hidden by CornerWindow"), 0
        if verb == "hide":
            name = taskbar.hide()
            return (f"{name} hidden (`cornerwindow taskbar show` brings it back)" if name
                    else "No supported taskbar found"), \
                0 if name else 1
        return taskbar.describe(), 0


def _flush() -> None:
    """Write pending GSettings changes now: restoring happens right before the process exits."""
    try:
        from gi.repository import Gio

        Gio.Settings.sync()
    except Exception:
        pass


def _name_of(uuid: str) -> str:
    for u, _schema, name in PANELS:
        if u == uuid:
            return name
    for u, name in DOCKS:
        if u == uuid:
            return name
    return uuid


def _extension_call(method: str, uuid: str) -> Any:
    from gi.repository import GLib

    from .. import dbus

    return dbus.call(dbus.session(), "org.gnome.Shell", "/org/gnome/Shell", "org.gnome.Shell.Extensions",
                     method, GLib.Variant("(s)", (uuid,)), "(b)")


def _extension_active(uuid: str) -> bool:
    """Is this GNOME Shell extension enabled and running? (False outside GNOME.)"""
    from gi.repository import GLib

    from .. import dbus

    try:
        info = dbus.call(dbus.session(), "org.gnome.Shell", "/org/gnome/Shell", "org.gnome.Shell.Extensions",
                         "GetExtensionInfo", GLib.Variant("(s)", (uuid,)), "(a{sv})", timeout_ms=1500)[0]
    except Exception:
        return False
    return int(info.get("state", 0)) == 1  # ExtensionState.ENABLED (ACTIVE since GNOME 45)


def _panel_settings(uuid: str, schema: str) -> _GSettings | None:
    """The extension's GSettings, from the system schemas or the extension's own folder."""
    from gi.repository import Gio

    if not schema:
        return None
    default = Gio.SettingsSchemaSource.get_default()
    found = default.lookup(schema, True) if default else None
    if found is None:
        for root in (Path.home() / ".local/share/gnome-shell/extensions", Path("/usr/share/gnome-shell/extensions")):
            folder = root / uuid / "schemas"
            if (folder / "gschemas.compiled").exists():
                try:
                    source = Gio.SettingsSchemaSource.new_from_directory(str(folder), default, False)
                except Exception as exc:
                    _log.debug("Schemas in %s: %s", folder, exc)
                    continue
                found = source.lookup(schema, False)
                if found is not None:
                    break
    if found is None:
        return None
    return _GSettings(Gio.Settings.new_full(found, None, None))
