"""Removable drives (USB sticks, SD cards, phones, external disks): mount, open, eject.
Hidden while nothing is plugged in. Event driven through Gio.VolumeMonitor."""

from __future__ import annotations

from gi.repository import Gio, Gtk

from ..core import log
from ..platform.desktop import open_uri
from ..ui.widgets import Card, IconButton, label
from ..ui.widgets.card import resolve_icon
from .base import Command, Module, module

_log = log.get("drives")


def _removable(volume: Gio.Volume) -> bool:
    drive = volume.get_drive()
    if drive is not None:
        return drive.is_removable() or drive.is_media_removable() or drive.can_eject()
    # no drive: phones and cameras over MTP/PTP (gvfs "device" class); network shares stay out
    return volume.can_eject() or (volume.get_identifier("class") == "device" and volume.can_mount())


@module("drives", title="Drives", icon="drive", description="USB sticks and external disks: open and eject")
class Drives(Module):
    def setup(self) -> None:
        self.monitor = Gio.VolumeMonitor.get()
        for signal in ("volume-added", "volume-removed", "volume-changed", "mount-added", "mount-removed",
                       "mount-changed"):
            self.monitor.connect(signal, lambda *_a: self._render())

    def build(self):
        self.revealer = Gtk.Revealer()
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.revealer.set_transition_duration(240)
        card = Card("Drives", spacing=4)
        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        card.pack_start(self.rows, False, False, 0)
        self.revealer.add(card)
        self._render()
        return self.revealer

    def volumes(self) -> list[Gio.Volume]:
        return [v for v in self.monitor.get_volumes() if _removable(v)]

    def _render(self) -> None:
        if not hasattr(self, "rows"):
            return
        for child in self.rows.get_children():
            self.rows.remove(child)
            child.destroy()
        volumes = self.volumes()
        for volume in volumes:
            self.rows.pack_start(self._row(volume), False, False, 0)
        self.rows.show_all()
        self.revealer.set_reveal_child(bool(volumes))

    def _row(self, volume: Gio.Volume) -> Gtk.Widget:
        mount = volume.get_mount()
        box = Gtk.Box(spacing=8)
        image = Gtk.Image.new_from_gicon(volume.get_symbolic_icon(), Gtk.IconSize.MENU) \
            if volume.get_symbolic_icon() else Gtk.Image.new_from_icon_name(resolve_icon("drive"), Gtk.IconSize.MENU)
        image.set_pixel_size(16)
        box.pack_start(image, False, False, 0)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        text.pack_start(label(volume.get_name() or "Drive", "cw-small", ellipsize=True), False, False, 0)
        detail = label("Mounted" if mount else "Not mounted", "cw-dim cw-small")
        text.pack_start(detail, False, False, 0)
        if mount is not None:
            _free_async(mount, detail)
        box.pack_start(text, True, True, 0)
        if mount is not None:
            box.pack_start(IconButton("folder", lambda m=mount: self.open(m), "Open", "cw-icon cw-plain", 14),
                           False, False, 0)
            box.pack_start(IconButton("eject", lambda v=volume, m=mount: self.eject(v, m), "Eject",
                                      "cw-icon cw-plain", 14), False, False, 0)
        elif volume.can_mount():
            box.pack_start(IconButton("folder", lambda v=volume: self.mount_and_open(v), "Mount and open",
                                      "cw-icon cw-plain", 14), False, False, 0)
        return box

    # -- actions --------------------------------------------------------------------------
    def open(self, mount: Gio.Mount) -> None:
        open_uri(mount.get_root().get_uri())
        self.ctx.island.close()

    def mount_and_open(self, volume: Gio.Volume) -> None:
        def done(vol, result) -> None:
            try:
                vol.mount_finish(result)
            except Exception as exc:
                self.ctx.notify("Could not mount", f"{vol.get_name()}: {exc}", "drive-removable-media-symbolic")
                return
            if vol.get_mount():
                self.open(vol.get_mount())

        volume.mount(Gio.MountMountFlags.NONE, Gio.MountOperation(), None, done)

    def eject(self, volume: Gio.Volume, mount: Gio.Mount) -> None:
        name = volume.get_name()

        def done(obj, result, finish) -> None:
            try:
                finish(result)
            except Exception as exc:
                self.ctx.notify("Could not eject", f"{name}: {exc}", "media-eject-symbolic")
                return
            self.ctx.notify("Safe to remove", f"{name} can be unplugged", "media-eject-symbolic")

        op = Gio.MountOperation()
        if mount.can_eject():
            mount.eject_with_operation(Gio.MountUnmountFlags.NONE, op, None, done, mount.eject_with_operation_finish)
        elif volume.can_eject():
            volume.eject_with_operation(Gio.MountUnmountFlags.NONE, op, None, done,
                                        volume.eject_with_operation_finish)
        else:
            mount.unmount_with_operation(Gio.MountUnmountFlags.NONE, op, None, done,
                                         mount.unmount_with_operation_finish)

    def commands(self) -> list[Command]:
        def eject(arg: str) -> str:
            arg = arg.strip().lower()
            mounted = [v for v in self.volumes() if v.get_mount() and (not arg or arg in v.get_name().lower())]
            if not mounted:
                return "No drive to eject"
            self.eject(mounted[0], mounted[0].get_mount())
            return f"Ejecting {mounted[0].get_name()}…"

        return [Command(("eject",), "Eject a drive", "eject · eject usb", eject, True)]


def _free_async(mount: Gio.Mount, target: Gtk.Label) -> None:
    """Fill in "12.3 GB free of 32 GB" without blocking (slow on phones over MTP)."""
    def done(root, result) -> None:
        try:
            info = root.query_filesystem_info_finish(result)
            free = info.get_attribute_uint64("filesystem::free")
            size = info.get_attribute_uint64("filesystem::size")
        except Exception:
            return
        if size:
            target.set_text(f"{_gb(free)} free of {_gb(size)}")

    from gi.repository import GLib

    mount.get_root().query_filesystem_info_async("filesystem::free,filesystem::size",
                                                 GLib.PRIORITY_DEFAULT, None, done)


def _gb(value: int) -> str:
    return f"{value / 1e9:.1f} GB" if value < 1e11 else f"{value / 1e9:.0f} GB"
