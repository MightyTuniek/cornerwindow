"""Live backdrop on X11: what is on screen *below* one of our windows, without us in it.

Grabbing the root window would include our own glass (feedback). Instead we do
what a compositor does, server side:

  * the windows stacked below ours come from XQueryTree(root), bottom to top.
    Mutter parks hidden windows under its "mutter guard window", so we start
    above that
  * each one's off-screen contents come from XCompositeNameWindowPixmap (the
    compositor already redirects every top-level) and are composited OVER the
    wallpaper with XRender, so ARGB windows and client-side shadows look right
  * the result is read back with XShmGetImage (~1 ms) straight into memory the
    glass kernel reads
  * XDamage on those windows plus SubstructureNotify on root tell us when
    something under a tracked rectangle changed. Nothing ticks otherwise

All of this uses a private Display connection whose fd is watched by GLib, so
GDK's connection is never touched. Errors on our connection (a window vanishing
mid-compose) are swallowed; errors on any other connection go to the previous
handler (GDK's).

Not captured: what GNOME Shell draws itself (top bar, taskbar, notifications,
window shadows drawn by Mutter). The wallpaper shows through there.
"""

from __future__ import annotations

import ctypes
from ctypes import POINTER, byref, c_char_p, c_int, c_long, c_short, c_uint, c_ulong, c_ushort, c_void_p
from typing import Callable

from gi.repository import GLib

from ...core import log

_log = log.get("backdrop")

Window = Pixmap = Picture = Damage = XID = c_ulong
Display = c_void_p

IsViewable = 2
InputOnly = 2
ZPixmap = 2
TrueColor = 4
PictOpSrc, PictOpOver = 1, 3
PictStandardARGB32 = 0
XDamageReportBoundingBox = 2
SubstructureNotifyMask = 1 << 19
ALL_PLANES = 0xFFFFFFFF
IPC_PRIVATE, IPC_CREAT, IPC_RMID = 0, 0o1000, 0
STRUCTURE_EVENTS = {16, 17, 18, 19, 21, 22, 23, 26}  # Create Destroy Unmap Map Reparent Configure Gravity Circulate


class XWindowAttributes(ctypes.Structure):
    _fields_ = [("x", c_int), ("y", c_int), ("width", c_int), ("height", c_int),
                ("border_width", c_int), ("depth", c_int), ("visual", c_void_p), ("root", Window),
                ("class_", c_int), ("bit_gravity", c_int), ("win_gravity", c_int),
                ("backing_store", c_int), ("backing_planes", c_ulong), ("backing_pixel", c_ulong),
                ("save_under", c_int), ("colormap", c_ulong), ("map_installed", c_int),
                ("map_state", c_int), ("all_event_masks", c_long), ("your_event_mask", c_long),
                ("do_not_propagate_mask", c_long), ("override_redirect", c_int), ("screen", c_void_p)]


class XImageFuncs(ctypes.Structure):
    _fields_ = [(name, c_void_p) for name in ("create_image", "destroy_image", "get_pixel",
                                               "put_pixel", "sub_image", "add_pixel")]


class XImage(ctypes.Structure):
    _fields_ = [("width", c_int), ("height", c_int), ("xoffset", c_int), ("format", c_int),
                ("data", c_void_p), ("byte_order", c_int), ("bitmap_unit", c_int),
                ("bitmap_bit_order", c_int), ("bitmap_pad", c_int), ("depth", c_int),
                ("bytes_per_line", c_int), ("bits_per_pixel", c_int), ("red_mask", c_ulong),
                ("green_mask", c_ulong), ("blue_mask", c_ulong), ("obdata", c_void_p),
                ("f", XImageFuncs)]


class XShmSegmentInfo(ctypes.Structure):
    _fields_ = [("shmseg", c_ulong), ("shmid", c_int), ("shmaddr", c_void_p), ("readOnly", c_int)]


class XVisualInfo(ctypes.Structure):
    _fields_ = [("visual", c_void_p), ("visualid", c_ulong), ("screen", c_int), ("depth", c_int),
                ("class_", c_int), ("red_mask", c_ulong), ("green_mask", c_ulong),
                ("blue_mask", c_ulong), ("colormap_size", c_int), ("bits_per_rgb", c_int)]


class XRectangle(ctypes.Structure):
    _fields_ = [("x", c_short), ("y", c_short), ("width", c_ushort), ("height", c_ushort)]


class XAnyEvent(ctypes.Structure):
    _fields_ = [("type", c_int), ("serial", c_ulong), ("send_event", c_int), ("display", c_void_p),
                ("window", Window)]


class XConfigureEvent(ctypes.Structure):
    _fields_ = [("type", c_int), ("serial", c_ulong), ("send_event", c_int), ("display", c_void_p),
                ("event", Window), ("window", Window), ("x", c_int), ("y", c_int),
                ("width", c_int), ("height", c_int), ("border_width", c_int), ("above", Window),
                ("override_redirect", c_int)]


class XDamageNotifyEvent(ctypes.Structure):
    _fields_ = [("type", c_int), ("serial", c_ulong), ("send_event", c_int), ("display", c_void_p),
                ("drawable", c_ulong), ("damage", Damage), ("level", c_int), ("more", c_int),
                ("timestamp", c_ulong), ("area", XRectangle), ("geometry", XRectangle)]


class XEvent(ctypes.Union):
    _fields_ = [("type", c_int), ("any", XAnyEvent), ("configure", XConfigureEvent),
                ("damage", XDamageNotifyEvent), ("pad", c_long * 24)]


class XErrorEvent(ctypes.Structure):
    _fields_ = [("type", c_int), ("display", c_void_p), ("resourceid", XID), ("serial", c_ulong),
                ("error_code", ctypes.c_ubyte), ("request_code", ctypes.c_ubyte),
                ("minor_code", ctypes.c_ubyte)]


ERROR_HANDLER = ctypes.CFUNCTYPE(c_int, c_void_p, POINTER(XErrorEvent))

Rect = tuple[int, int, int, int]  # x, y, w, h in root coordinates


def intersect(a: Rect, b: Rect) -> Rect | None:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1 - x0, y1 - y0)


def bounds(a: Rect | None, b: Rect | None) -> Rect | None:
    if a is None or b is None:
        return a or b
    x0, y0 = min(a[0], b[0]), min(a[1], b[1])
    x1, y1 = max(a[0] + a[2], b[0] + b[2]), max(a[1] + a[3], b[1] + b[3])
    return (x0, y0, x1 - x0, y1 - y0)


def contains(outer: Rect, inner: Rect) -> bool:
    return (outer[0] <= inner[0] and outer[1] <= inner[1]
            and outer[0] + outer[2] >= inner[0] + inner[2]
            and outer[1] + outer[3] >= inner[1] + inner[3])


class _Lib:
    """The handful of Xlib/extension calls we need, with prototypes."""

    def __init__(self) -> None:
        self.x = x = ctypes.CDLL("libX11.so.6")
        self.xc = xc = ctypes.CDLL("libXcomposite.so.1")
        self.xd = xd = ctypes.CDLL("libXdamage.so.1")
        self.xr = xr = ctypes.CDLL("libXrender.so.1")
        self.xe = xe = ctypes.CDLL("libXext.so.6")
        self.libc = libc = ctypes.CDLL(None)

        def proto(lib, name, res, *args):
            fn = getattr(lib, name)
            fn.restype = res
            fn.argtypes = list(args)

        proto(x, "XOpenDisplay", Display, c_char_p)
        proto(x, "XCloseDisplay", c_int, Display)
        proto(x, "XConnectionNumber", c_int, Display)
        proto(x, "XDefaultRootWindow", Window, Display)
        proto(x, "XDefaultScreen", c_int, Display)
        proto(x, "XQueryTree", c_int, Display, Window, POINTER(Window), POINTER(Window),
              POINTER(POINTER(Window)), POINTER(c_uint))
        proto(x, "XFree", c_int, c_void_p)
        proto(x, "XGetWindowAttributes", c_int, Display, Window, POINTER(XWindowAttributes))
        proto(x, "XFetchName", c_int, Display, Window, POINTER(c_char_p))
        proto(x, "XSelectInput", c_int, Display, Window, c_long)
        proto(x, "XPending", c_int, Display)
        proto(x, "XNextEvent", c_int, Display, POINTER(XEvent))
        proto(x, "XFlush", c_int, Display)
        proto(x, "XSync", c_int, Display, c_int)
        proto(x, "XCreatePixmap", Pixmap, Display, Window, c_uint, c_uint, c_uint)
        proto(x, "XFreePixmap", c_int, Display, Pixmap)
        proto(x, "XGetImage", POINTER(XImage), Display, c_ulong, c_int, c_int, c_uint, c_uint,
              c_ulong, c_int)
        proto(x, "XMatchVisualInfo", c_int, Display, c_int, c_int, c_int, POINTER(XVisualInfo))
        proto(x, "XCreateImage", POINTER(XImage), Display, c_void_p, c_uint, c_int, c_int, c_void_p,
              c_uint, c_uint, c_int, c_int)
        proto(x, "XCreateGC", c_void_p, Display, c_ulong, c_ulong, c_void_p)
        proto(x, "XFreeGC", c_int, Display, c_void_p)
        proto(x, "XPutImage", c_int, Display, c_ulong, c_void_p, POINTER(XImage), c_int, c_int,
              c_int, c_int, c_uint, c_uint)
        proto(x, "XSetErrorHandler", c_void_p, c_void_p)

        proto(xc, "XCompositeQueryExtension", c_int, Display, POINTER(c_int), POINTER(c_int))
        proto(xc, "XCompositeNameWindowPixmap", Pixmap, Display, Window)
        proto(xd, "XDamageQueryExtension", c_int, Display, POINTER(c_int), POINTER(c_int))
        proto(xd, "XDamageCreate", Damage, Display, c_ulong, c_int)
        proto(xd, "XDamageDestroy", None, Display, Damage)
        proto(xd, "XDamageSubtract", None, Display, Damage, c_ulong, c_ulong)
        proto(xr, "XRenderQueryExtension", c_int, Display, POINTER(c_int), POINTER(c_int))
        proto(xr, "XRenderFindVisualFormat", c_void_p, Display, c_void_p)
        proto(xr, "XRenderFindStandardFormat", c_void_p, Display, c_int)
        proto(xr, "XRenderCreatePicture", Picture, Display, c_ulong, c_void_p, c_ulong, c_void_p)
        proto(xr, "XRenderFreePicture", None, Display, Picture)
        proto(xr, "XRenderComposite", None, Display, c_int, Picture, Picture, Picture,
              c_int, c_int, c_int, c_int, c_int, c_int, c_uint, c_uint)
        proto(xe, "XShmQueryExtension", c_int, Display)
        proto(xe, "XShmCreateImage", POINTER(XImage), Display, c_void_p, c_uint, c_int, c_void_p,
              POINTER(XShmSegmentInfo), c_uint, c_uint)
        proto(xe, "XShmAttach", c_int, Display, POINTER(XShmSegmentInfo))
        proto(xe, "XShmDetach", c_int, Display, POINTER(XShmSegmentInfo))
        proto(xe, "XShmGetImage", c_int, Display, c_ulong, POINTER(XImage), c_int, c_int, c_ulong)
        proto(libc, "shmget", c_int, c_int, ctypes.c_size_t, c_int)
        proto(libc, "shmat", c_void_p, c_int, c_void_p, c_int)
        proto(libc, "shmdt", c_int, c_void_p)
        proto(libc, "shmctl", c_int, c_int, c_int, c_void_p)


def destroy_image(image: POINTER(XImage)) -> None:
    """XDestroyImage is a macro calling image->f.destroy_image."""
    if not image:
        return
    fn = ctypes.CFUNCTYPE(c_int, POINTER(XImage))(image.contents.f.destroy_image)
    fn(image)


class _Layer:
    __slots__ = ("xid", "x", "y", "w", "h", "border", "visual", "pixmap", "picture", "damage", "ok")

    def __init__(self, xid: int) -> None:
        self.xid = xid
        self.x = self.y = self.w = self.h = self.border = 0
        self.visual = None
        self.pixmap = self.picture = self.damage = 0
        self.ok = True

    def rect(self) -> Rect:
        b = self.border * 2
        return (self.x, self.y, self.w + b, self.h + b)


class Track:
    """One of our windows whose glass needs what is below it.

    `area()` returns the root-coordinate rectangle we may ever read (e.g. the
    whole island window); damage outside it is ignored. `capture(rect)` returns
    the pixels of `rect` (inside `area`) as (address, width, height, stride).
    """

    def __init__(self, owner: "X11Backdrop", xid_fn: Callable[[], int], area_fn: Callable[[], Rect | None],
                 on_dirty: Callable[[], None]) -> None:
        self.owner = owner
        self.xid_fn = xid_fn
        self.area_fn = area_fn
        self.on_dirty = on_dirty
        self.dirty = True
        self.generation = 0
        self.rect: Rect | None = None
        self.pixmap = 0
        self.picture = 0
        self.size = (0, 0)
        self.shm: XShmSegmentInfo | None = None
        self.image = None
        self.fallback = None  # bytes from XGetImage when XShm is unavailable

    def untrack(self) -> None:
        self.owner._untrack(self)

    def invalidate(self) -> None:
        if not self.dirty:
            self.dirty = True
            try:
                self.on_dirty()
            except Exception:
                _log.exception("Backdrop dirty callback failed")

    def capture(self, rect: Rect, refresh: bool = True):
        return self.owner._capture(self, rect, refresh)


class X11Backdrop:
    name = "x11"
    live = True

    def __init__(self, wallpaper) -> None:
        self.lib = _Lib()
        x = self.lib.x
        self.dpy = x.XOpenDisplay(None)
        if not self.dpy:
            raise RuntimeError("cannot open X display")
        ev, err = c_int(), c_int()
        for query, what in ((self.lib.xc.XCompositeQueryExtension, "Composite"),
                            (self.lib.xr.XRenderQueryExtension, "Render")):
            if not query(self.dpy, byref(ev), byref(err)):
                raise RuntimeError(f"X server lacks {what}")
        if not self.lib.xd.XDamageQueryExtension(self.dpy, byref(ev), byref(err)):
            raise RuntimeError("X server lacks Damage")
        self.damage_event = ev.value  # XDamageNotify = event base + 0
        self.has_shm = bool(self.lib.xe.XShmQueryExtension(self.dpy))
        self.root = x.XDefaultRootWindow(self.dpy)
        self.screen = x.XDefaultScreen(self.dpy)
        vinfo = XVisualInfo()
        if not x.XMatchVisualInfo(self.dpy, self.screen, 32, TrueColor, byref(vinfo)):
            raise RuntimeError("no 32-bit visual")
        self.argb_visual = vinfo.visual
        self.argb = self.lib.xr.XRenderFindStandardFormat(self.dpy, PictStandardARGB32)
        self._install_error_handler()
        x.XSelectInput(self.dpy, self.root, SubstructureNotifyMask)
        x.XFlush(self.dpy)

        self.wallpaper = wallpaper
        self.wall_pixmap = 0
        self.wall_picture = 0
        self.wall_key = None
        self.tracks: list[Track] = []
        self.layers: dict[int, _Layer] = {}
        self.order: list[int] = []  # root children, bottom to top
        self.guard: int | None = None
        self.stack_dirty = True
        self._top_cache: dict[int, int] = {}
        self._event = XEvent()
        channel = GLib.IOChannel.unix_new(x.XConnectionNumber(self.dpy))
        self._watch = GLib.io_add_watch(channel, GLib.PRIORITY_DEFAULT, GLib.IO_IN, self._on_events)
        wallpaper.on_change(self._wallpaper_changed)
        _log.info("Live backdrop: X11 Composite%s", " + XShm" if self.has_shm else "")

    # == errors ==========================================================================
    def _install_error_handler(self) -> None:
        holder: dict = {}

        def handler(display, event) -> int:
            if display == self.dpy:
                return 0  # a window went away mid-compose: harmless
            previous = holder.get("previous")
            if previous:
                return ERROR_HANDLER(previous)(display, event)
            return 0

        self._handler = ERROR_HANDLER(handler)  # keep alive
        holder["previous"] = self.lib.x.XSetErrorHandler(ctypes.cast(self._handler, c_void_p))

    # == tracking ========================================================================
    def track(self, xid_fn: Callable[[], int], area_fn: Callable[[], Rect | None],
              on_dirty: Callable[[], None]) -> Track:
        track = Track(self, xid_fn, area_fn, on_dirty)
        self.tracks.append(track)
        self.stack_dirty = True
        return track

    def _untrack(self, track: Track) -> None:
        if track in self.tracks:
            self.tracks.remove(track)
        self._free_track(track)
        if not self.tracks:
            self._drop_layers()

    def _free_track(self, track: Track) -> None:
        lib, dpy = self.lib, self.dpy
        if track.picture:
            lib.xr.XRenderFreePicture(dpy, track.picture)
        if track.pixmap:
            lib.x.XFreePixmap(dpy, track.pixmap)
        track.picture = track.pixmap = 0
        if track.shm is not None:
            lib.xe.XShmDetach(dpy, byref(track.shm))
            lib.x.XSync(dpy, 0)
            if track.image:
                track.image.contents.data = None
                destroy_image(track.image)
            lib.libc.shmdt(track.shm.shmaddr)
        track.shm = None
        track.image = None
        track.size = (0, 0)

    # == stacking ========================================================================
    def _query_order(self) -> None:
        x = self.lib.x
        root_ret, parent_ret = Window(), Window()
        children = POINTER(Window)()
        count = c_uint()
        if not x.XQueryTree(self.dpy, self.root, byref(root_ret), byref(parent_ret), byref(children),
                            byref(count)):
            self.order = []
            return
        self.order = [children[i] for i in range(count.value)]
        if children:
            x.XFree(children)
        alive = set(self.order)
        for xid in [w for w in self.layers if w not in alive]:
            self._free_layer(self.layers.pop(xid))
        if self.guard not in alive:
            self.guard = None
            for xid in self.order:
                name = c_char_p()
                if x.XFetchName(self.dpy, xid, byref(name)) and name.value:
                    found = name.value == b"mutter guard window"
                    x.XFree(name)
                    if found:
                        self.guard = xid
                        break

    def _toplevel(self, xid: int) -> int:
        """The root child that contains our window (it may be reparented into a frame)."""
        cached = self._top_cache.get(xid)
        if cached is not None:
            return cached
        x = self.lib.x
        current = xid
        for _ in range(8):
            root_ret, parent_ret = Window(), Window()
            children = POINTER(Window)()
            count = c_uint()
            if not x.XQueryTree(self.dpy, current, byref(root_ret), byref(parent_ret), byref(children),
                                byref(count)):
                return xid
            if children:
                x.XFree(children)
            if parent_ret.value in (0, self.root):
                break
            current = parent_ret.value
        self._top_cache[xid] = current
        return current

    def _layer(self, xid: int) -> _Layer | None:
        layer = self.layers.get(xid)
        if layer is None:
            layer = _Layer(xid)
            attrs = XWindowAttributes()
            if not self.lib.x.XGetWindowAttributes(self.dpy, xid, byref(attrs)):
                layer.ok = False
            else:
                layer.x, layer.y, layer.w, layer.h = attrs.x, attrs.y, attrs.width, attrs.height
                layer.border = attrs.border_width
                layer.visual = attrs.visual
                layer.ok = attrs.map_state == IsViewable and attrs.class_ != InputOnly
            self.layers[xid] = layer
        return layer if layer.ok else None

    def _free_layer(self, layer: _Layer) -> None:
        lib, dpy = self.lib, self.dpy
        if layer.damage:
            lib.xd.XDamageDestroy(dpy, layer.damage)
        if layer.picture:
            lib.xr.XRenderFreePicture(dpy, layer.picture)
        if layer.pixmap:
            lib.x.XFreePixmap(dpy, layer.pixmap)
        layer.damage = layer.picture = layer.pixmap = 0

    def _drop_layers(self) -> None:
        for layer in self.layers.values():
            self._free_layer(layer)
        self.layers.clear()
        self.stack_dirty = True

    def _ensure_picture(self, layer: _Layer) -> bool:
        if layer.picture:
            return True
        lib, dpy = self.lib, self.dpy
        fmt = lib.xr.XRenderFindVisualFormat(dpy, layer.visual)
        if not fmt:
            layer.ok = False
            return False
        layer.pixmap = lib.xc.XCompositeNameWindowPixmap(dpy, layer.xid)
        if not layer.pixmap:
            layer.ok = False
            return False
        layer.picture = lib.xr.XRenderCreatePicture(dpy, layer.pixmap, fmt, 0, None)
        return bool(layer.picture)

    def _below(self, track: Track) -> list[_Layer]:
        """Viewable windows under the track's window, bottom to top, that touch its area."""
        if self.stack_dirty:
            self._query_order()
            self.stack_dirty = False
        try:
            own = self._toplevel(int(track.xid_fn()))
        except Exception:
            return []
        # also watch what we captured last, so a shrunken area (panel closed) can't
        # leave stale pixels behind for the next time it grows
        area = bounds(track.area_fn(), track.rect)
        if area is None:
            return []
        start = self.order.index(self.guard) + 1 if self.guard in self.order else 0
        result = []
        for xid in self.order[start:]:
            if xid == own:
                break
            layer = self._layer(xid)
            if layer is None or intersect(layer.rect(), area) is None:
                continue
            if not layer.damage:
                layer.damage = self.lib.xd.XDamageCreate(self.dpy, xid, XDamageReportBoundingBox)
            result.append(layer)
        return result

    # == events ==========================================================================
    def _on_events(self, *_args) -> bool:
        x = self.lib.x
        event = self._event
        dirty_rects: list[Rect] = []
        restack = False
        while x.XPending(self.dpy):
            x.XNextEvent(self.dpy, byref(event))
            kind = event.type
            if kind == self.damage_event:
                d = event.damage
                self.lib.xd.XDamageSubtract(self.dpy, d.damage, 0, 0)
                layer = self.layers.get(d.drawable)
                if layer is not None:
                    dirty_rects.append((layer.x + d.area.x, layer.y + d.area.y, d.area.width, d.area.height))
            elif kind in STRUCTURE_EVENTS:
                restack = True
                if kind == 21:  # reparented: our window may have gained a frame
                    self._top_cache.clear()
                window = event.configure.window if kind == 22 else event.any.window
                layer = self.layers.get(window)
                if kind == 22 and layer is not None:
                    c = event.configure
                    old = layer.rect()
                    moved_only = (c.width, c.height) == (layer.w, layer.h)
                    layer.x, layer.y, layer.w, layer.h = c.x, c.y, c.width, c.height
                    if not moved_only:  # the named pixmap is stale after a resize
                        self._free_layer(layer)
                    dirty_rects.extend((old, layer.rect()))
                elif layer is not None:
                    dirty_rects.append(layer.rect())
                    if kind in (17, 18, 21):  # destroyed, unmapped, reparented
                        self._free_layer(layer)
                        self.layers.pop(window, None)
                    elif kind == 19:  # mapped again: re-read its attributes
                        self._free_layer(layer)
                        self.layers.pop(window, None)
                elif kind == 22:
                    c = event.configure
                    if c.window not in self._own_windows():
                        dirty_rects.append((c.x, c.y, c.width + 2 * c.border_width,
                                            c.height + 2 * c.border_width))
                else:
                    dirty_rects.append((-1 << 20, -1 << 20, 1 << 21, 1 << 21))
        if restack:
            self.stack_dirty = True
        if dirty_rects:
            for track in list(self.tracks):
                area = track.area_fn()
                if area is not None and any(intersect(area, r) for r in dirty_rects):
                    track.invalidate()
                elif track.rect is not None and any(intersect(track.rect, r) for r in dirty_rects):
                    track.dirty = True  # outside what's shown now: refresh on next use, no redraw
        return True

    def _own_windows(self) -> set[int]:
        own = set()
        for track in self.tracks:
            try:
                own.add(self._toplevel(int(track.xid_fn())))
            except Exception:
                pass
        return own

    def _wallpaper_changed(self) -> None:
        self.wall_key = None
        for track in list(self.tracks):
            track.invalidate()

    # == composing =======================================================================
    def _ensure_wallpaper(self) -> bool:
        surface = self.wallpaper.surface()
        if surface is None:
            return False
        key = id(surface)
        if key == self.wall_key and self.wall_picture:
            return True
        lib, dpy = self.lib, self.dpy
        if self.wall_picture:
            lib.xr.XRenderFreePicture(dpy, self.wall_picture)
        if self.wall_pixmap:
            lib.x.XFreePixmap(dpy, self.wall_pixmap)
        w, h = surface.get_width(), surface.get_height()
        self.wall_pixmap = lib.x.XCreatePixmap(dpy, self.root, w, h, 32)
        data = surface.get_data()
        buffer = (ctypes.c_char * len(data)).from_buffer(data)
        image = lib.x.XCreateImage(dpy, self.argb_visual, 32, ZPixmap, 0, ctypes.addressof(buffer),
                                   w, h, 32, surface.get_stride())
        gc = lib.x.XCreateGC(dpy, self.wall_pixmap, 0, None)
        lib.x.XPutImage(dpy, self.wall_pixmap, gc, image, 0, 0, 0, 0, w, h)
        lib.x.XFreeGC(dpy, gc)
        image.contents.data = None
        destroy_image(image)
        self.wall_picture = lib.xr.XRenderCreatePicture(dpy, self.wall_pixmap, self.argb, 0, None)
        self.wall_key = key
        self._wall_surface = surface  # keep it alive while its id is our key
        return True

    def _ensure_target(self, track: Track, w: int, h: int) -> bool:
        if track.size[0] >= w and track.size[1] >= h and track.pixmap:
            return True
        self._free_track(track)
        lib, dpy = self.lib, self.dpy
        # grow in steps so a growing panel doesn't reallocate every frame
        w = max(w, (track.size[0] or 0), 64)
        h = max(h, (track.size[1] or 0), 64)
        area = track.area_fn()
        if area is not None:
            w, h = max(w, area[2]), max(h, area[3])
        track.pixmap = lib.x.XCreatePixmap(dpy, self.root, w, h, 32)
        track.picture = lib.xr.XRenderCreatePicture(dpy, track.pixmap, self.argb, 0, None)
        track.size = (w, h)
        if self.has_shm:
            info = XShmSegmentInfo()
            image = lib.xe.XShmCreateImage(dpy, self.argb_visual, 32, ZPixmap, None, byref(info), w, h)
            if image:
                size = image.contents.bytes_per_line * h
                info.shmid = lib.libc.shmget(IPC_PRIVATE, size, IPC_CREAT | 0o600)
                if info.shmid >= 0:
                    info.shmaddr = lib.libc.shmat(info.shmid, None, 0)
                    image.contents.data = info.shmaddr
                    info.readOnly = 0
                    lib.xe.XShmAttach(dpy, byref(info))
                    lib.x.XSync(dpy, 0)
                    lib.libc.shmctl(info.shmid, IPC_RMID, None)  # freed once both sides detach
                    track.shm, track.image = info, image
                    return True
                image.contents.data = None
                destroy_image(image)
        return True

    def _capture(self, track: Track, rect: Rect, refresh: bool = True):
        if track.rect is not None and contains(track.rect, rect) and (not track.dirty or not refresh):
            return self._result(track, rect)
        layers = self._below(track)
        x0, y0, w, h = rect
        if w <= 0 or h <= 0 or not self._ensure_target(track, w, h):
            return None
        lib, dpy = self.lib, self.dpy
        if self._ensure_wallpaper():
            lib.xr.XRenderComposite(dpy, PictOpSrc, self.wall_picture, 0, track.picture,
                                    x0, y0, 0, 0, 0, 0, w, h)
        for layer in layers:
            part = intersect(layer.rect(), rect)
            if part is None or not self._ensure_picture(layer):
                continue
            px, py, pw, ph = part
            lib.xr.XRenderComposite(dpy, PictOpOver, layer.picture, 0, track.picture,
                                    px - layer.x, py - layer.y, 0, 0, px - x0, py - y0, pw, ph)
        if track.image is not None:
            img = track.image.contents
            img.width, img.height, img.bytes_per_line = w, h, w * 4
            lib.xe.XShmGetImage(dpy, track.pixmap, track.image, 0, 0, ALL_PLANES)
        else:
            image = lib.x.XGetImage(dpy, track.pixmap, 0, 0, w, h, ALL_PLANES, ZPixmap)
            if not image:
                return None
            img = image.contents
            track.fallback = ctypes.create_string_buffer(ctypes.string_at(img.data, img.bytes_per_line * h))
            track.fallback_stride = img.bytes_per_line
            destroy_image(image)
        track.rect = rect
        track.dirty = False
        track.generation += 1
        return self._result(track, rect)

    def _result(self, track: Track, rect: Rect):
        """(address, w, h, stride, origin_x, origin_y, generation) of the captured rect."""
        tx, ty, tw, th = track.rect
        if track.image is not None:
            base, stride = track.shm.shmaddr, tw * 4
        else:
            base, stride = ctypes.addressof(track.fallback), track.fallback_stride
        return (base, tw, th, stride, tx, ty, track.generation)

    def close(self) -> None:
        for track in list(self.tracks):
            self._free_track(track)
        self._drop_layers()
        if self._watch:
            GLib.source_remove(self._watch)
            self._watch = 0
