"""Now playing (MPRIS): artwork, title, controls, progress. Hidden when nothing plays."""

from __future__ import annotations

import hashlib
import urllib.parse
import urllib.request

from gi.repository import GdkPixbuf, Gtk

from ..core import paths, tasks
from ..ui.draw import pill, rounded_rect
from ..ui.widgets import Card, IconButton, label
from .base import Command, Module, module

ART = 52


@module("media", title="Now Playing", icon="music")
class Media(Module):
    def setup(self) -> None:
        self.svc = self.ctx.services.media
        self.now = None
        self._art_url = None
        self._art = None
        self.ctx.actions.register("media.play_pause", self.svc.play_pause, "Play / pause")
        self.ctx.actions.register("media.next", self.svc.next, "Next track")
        self.ctx.actions.register("media.previous", self.svc.previous, "Previous track")
        self.ctx.bus.subscribe("media.changed", self._changed)

    def build(self):
        self.revealer = Gtk.Revealer()
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.revealer.set_transition_duration(260)
        card = Card()
        top = Gtk.Box(spacing=10)
        self.art = Gtk.DrawingArea()
        self.art.set_size_request(ART, ART)
        self.art.connect("draw", self._draw_art)
        top.pack_start(self.art, False, False, 0)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        text.set_valign(Gtk.Align.CENTER)
        self.title = label("", "cw-track", ellipsize=True)
        self.artist = label("", "cw-artist", ellipsize=True)
        text.pack_start(self.title, False, False, 0)
        text.pack_start(self.artist, False, False, 0)
        top.pack_start(text, True, True, 0)
        controls = Gtk.Box(spacing=2)
        controls.set_valign(Gtk.Align.CENTER)
        controls.pack_start(IconButton("previous", self.svc.previous, css="cw-icon cw-plain"), False, False, 0)
        self.play = IconButton("play", self.svc.play_pause, css="cw-icon cw-plain", size=20)
        controls.pack_start(self.play, False, False, 0)
        controls.pack_start(IconButton("next", self.svc.next, css="cw-icon cw-plain"), False, False, 0)
        top.pack_start(controls, False, False, 0)
        card.pack_start(top, False, False, 0)
        self.bar = Gtk.DrawingArea()
        self.bar.set_size_request(-1, 4)
        self.bar.connect("draw", self._draw_bar)
        card.pack_start(self.bar, False, False, 4)
        self.revealer.add(card)
        self.ctx.every(0.5, self.bar.queue_draw)
        self.ctx.every(5.0, self.svc.refresh_position, immediately=False)
        self._changed(self.svc.current())
        return self.revealer

    def _changed(self, now=None) -> None:
        self.now = now
        if not hasattr(self, "revealer"):
            return
        if now is None or not now.title:
            self.revealer.set_reveal_child(False)
            return
        self.title.set_text(now.title)
        self.artist.set_text(now.artist or now.identity.title())
        self.play.set_icon("pause" if now.playing else "play")
        self.revealer.set_reveal_child(True)
        if now.art_url != self._art_url:
            self._art_url = now.art_url
            self._art = None
            self.art.queue_draw()
            if now.art_url:
                url = now.art_url
                tasks.run_async(lambda: _load_art(url), lambda pix: self._art_loaded(url, pix))
        self.bar.queue_draw()

    def _art_loaded(self, url: str, pixbuf) -> None:
        if url == self._art_url:
            self._art = pixbuf
            self.art.queue_draw()

    def _draw_art(self, widget, cr) -> bool:
        rounded_rect(cr, 0, 0, ART, ART, 10)
        cr.clip()
        if self._art is not None:
            from gi.repository import Gdk

            Gdk.cairo_set_source_pixbuf(cr, self._art, (ART - self._art.get_width()) / 2,
                                        (ART - self._art.get_height()) / 2)
            cr.paint()
        else:
            from ..ui import icons, theme

            p = theme.current()
            cr.set_source_rgba(*p.track)
            cr.paint()
            icons.paint(cr, "music", ART / 2, ART / 2, 22, p.text_dim, widget.get_scale_factor())
        return True

    def _draw_bar(self, widget, cr) -> bool:
        now = self.now
        w, h = widget.get_allocated_width(), widget.get_allocated_height()
        from ..ui import theme

        p = theme.current()
        pill(cr, 0, 0, w, h)
        cr.set_source_rgba(*p.track)
        cr.fill()
        if now and now.length > 0:
            frac = max(0.0, min(1.0, now.position_now() / now.length))
            pill(cr, 0, 0, max(h, w * frac), h)
            cr.set_source_rgba(*p.text[:3], 0.85)
            cr.fill()
        return True

    def on_open(self) -> None:
        self.svc.refresh_position()

    def commands(self) -> list[Command]:
        return [
            Command(("play", "pause"), "Play / pause", "play", lambda _a: self.svc.play_pause()),
            Command(("next",), "Next track", "next", lambda _a: self.svc.next()),
            Command(("prev", "previous"), "Previous track", "prev", lambda _a: self.svc.previous()),
        ]


def _load_art(url: str):
    cache = paths.runtime_dir() / "art"
    cache.mkdir(exist_ok=True)
    if url.startswith("file://"):
        path = urllib.parse.unquote(urllib.parse.urlparse(url).path)
    elif url.startswith(("http://", "https://")):
        path = str(cache / hashlib.sha1(url.encode()).hexdigest())
        try:
            open(path, "rb").close()
        except OSError:
            with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310 (player supplied art)
                data = response.read(4 * 1024 * 1024)
            with open(path, "wb") as fh:
                fh.write(data)
    else:
        return None
    pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
    w, h = pixbuf.get_width(), pixbuf.get_height()
    scale = ART / min(w, h)
    return pixbuf.scale_simple(max(int(w * scale), ART), max(int(h * scale), ART),
                               GdkPixbuf.InterpType.BILINEAR)
