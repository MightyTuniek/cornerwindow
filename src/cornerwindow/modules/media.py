"""Now playing: artwork, title, source, controls, seekable progress. Hidden when nothing plays.

Works with every MPRIS player (Spotify, VLC, mpv, Cider, KDE Connect, browser tabs on
YouTube, SoundCloud, Apple Music web...) and MPD. With several players open, chips on
top switch between them.
"""

from __future__ import annotations

import hashlib
import urllib.parse
import urllib.request

from gi.repository import Gdk, GdkPixbuf, Gio, Gtk

from ..core import paths, tasks
from ..core.activity import GREEN
from ..platform.media.model import fmt_time, parse_seek
from ..ui.draw import pill, rounded_rect
from ..ui.widgets import Card, IconButton, label
from ..ui.widgets.card import set_active
from .base import Command, Module, module

ART = 52
BAR = 14  # hit area of the progress bar; the drawn pill is thinner


@module("media", title="Now Playing", icon="music", description="What's playing in any player, with controls")
class Media(Module):
    def setup(self) -> None:
        self.svc = self.ctx.services.media
        self.now = None
        self._art_url = None
        self._art = None
        self._chips_key = None
        self._dot = False
        self._drag: float | None = None  # 0..1 while dragging the progress bar
        self._bar_hover = False
        actions = self.ctx.actions
        actions.register("media.play_pause", self.svc.play_pause, "Play / pause")
        actions.register("media.next", self.svc.next, "Next track")
        actions.register("media.previous", self.svc.previous, "Previous track")
        actions.register("media.forward", lambda: self.svc.seek_by(10), "Skip forward 10 s")
        actions.register("media.back", lambda: self.svc.seek_by(-10), "Skip back 10 s")
        actions.register("media.shuffle", self.svc.toggle_shuffle, "Shuffle on / off")
        actions.register("media.repeat", self.svc.cycle_loop, "Repeat: off / all / one")
        actions.register("media.raise", self.svc.raise_player, "Show the player")
        self.ctx.bus.subscribe("media.changed", self._changed)

    def build(self):
        self.revealer = Gtk.Revealer()
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.revealer.set_transition_duration(260)
        card = Card(spacing=6)

        self.chips = Gtk.Box(spacing=4)
        self.chips.set_no_show_all(True)
        card.pack_start(self.chips, False, False, 0)

        top = Gtk.Box(spacing=10)
        art_box = Gtk.EventBox()
        art_box.set_visible_window(False)
        art_box.connect("button-release-event", self._art_clicked)
        self.art = Gtk.DrawingArea()
        self.art.set_size_request(ART, ART)
        self.art.connect("draw", self._draw_art)
        art_box.add(self.art)
        top.pack_start(art_box, False, False, 0)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        text.set_valign(Gtk.Align.CENTER)
        self.title = label("", "cw-track", ellipsize=True)
        self.artist = label("", "cw-artist", ellipsize=True)
        self.source = label("", "cw-dim cw-small", ellipsize=True)
        for widget in (self.title, self.artist, self.source):
            text.pack_start(widget, False, False, 0)
        top.pack_start(text, True, True, 0)
        controls = Gtk.Box(spacing=2)
        controls.set_valign(Gtk.Align.CENTER)
        self.prev = IconButton("previous", self.svc.previous, css="cw-icon cw-plain")
        self.play = IconButton("play", self.svc.play_pause, css="cw-icon cw-plain", size=20)
        self.next = IconButton("next", self.svc.next, css="cw-icon cw-plain")
        for widget in (self.prev, self.play, self.next):
            controls.pack_start(widget, False, False, 0)
        top.pack_start(controls, False, False, 0)
        card.pack_start(top, False, False, 0)

        row = Gtk.Box(spacing=6)
        self.shuffle = IconButton("shuffle", self.svc.toggle_shuffle, "Shuffle", "cw-icon cw-plain cw-mini", 13)
        self.shuffle.set_no_show_all(True)
        row.pack_start(self.shuffle, False, False, 0)
        self.elapsed = label("", "cw-dim cw-small cw-tnum")
        row.pack_start(self.elapsed, False, False, 0)
        self.bar = Gtk.DrawingArea()
        self.bar.set_size_request(-1, BAR)
        self.bar.set_valign(Gtk.Align.CENTER)
        self.bar.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                            | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.SCROLL_MASK
                            | Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.bar.connect("draw", self._draw_bar)
        self.bar.connect("button-press-event", self._bar_press)
        self.bar.connect("motion-notify-event", self._bar_motion)
        self.bar.connect("button-release-event", self._bar_release)
        self.bar.connect("scroll-event", self._bar_scroll)
        self.bar.connect("enter-notify-event", lambda *_: self._set_bar_hover(True))
        self.bar.connect("leave-notify-event", lambda *_: self._set_bar_hover(False))
        row.pack_start(self.bar, True, True, 0)
        self.remaining = label("", "cw-dim cw-small cw-tnum")
        row.pack_start(self.remaining, False, False, 0)
        self.repeat = IconButton("repeat", self.svc.cycle_loop, "Repeat", "cw-icon cw-plain cw-mini", 13)
        self.repeat.set_no_show_all(True)
        row.pack_start(self.repeat, False, False, 0)
        card.pack_start(row, False, False, 0)

        self.revealer.add(card)
        self._chips_key = None
        self.ctx.every(0.5, self._tick)
        self.ctx.every(5.0, self.svc.refresh_position, immediately=False)
        self._changed(self.svc.current())
        return self.revealer

    # -- state ----------------------------------------------------------------------
    def _changed(self, now=None) -> None:
        self.now = now
        self._update_dot(now)
        if not hasattr(self, "revealer"):
            return
        if now is None or not now.title:
            self.revealer.set_reveal_child(False)
            self._render_chips()
            return
        self.title.set_text(now.title)
        self.artist.set_text(now.artist or now.album or "")
        self.artist.set_visible(bool(now.artist or now.album))
        self.source.set_text(now.source)
        self.play.set_icon("pause" if now.playing else "play")
        self.prev.set_sensitive(now.can_prev)
        self.next.set_sensitive(now.can_next)
        self.shuffle.set_visible(now.shuffle is not None)
        if now.shuffle is not None:
            set_active(self.shuffle, False)
            _set_dim(self.shuffle, not now.shuffle)
            self.shuffle.set_tooltip_text("Shuffle: " + ("on" if now.shuffle else "off"))
        self.repeat.set_visible(now.loop is not None)
        if now.loop is not None:
            self.repeat.set_icon("repeat-one" if now.loop == "Track" else "repeat")
            _set_dim(self.repeat, now.loop == "None")
            self.repeat.set_tooltip_text({"None": "Repeat: off", "Playlist": "Repeat: all",
                                          "Track": "Repeat: this track"}.get(now.loop, "Repeat"))
        self.revealer.set_reveal_child(True)
        self._render_chips()
        if now.art_url != self._art_url:
            self._art_url = now.art_url
            self._art = None
            self.art.queue_draw()
            if now.art_url:
                url = now.art_url
                tasks.run_async(lambda: _load_art(url), lambda pix: self._art_loaded(url, pix))
        self._tick()

    def _update_dot(self, now) -> None:
        want = bool(now and now.playing and self.config.get("trigger_dot", True))
        if want == self._dot:
            return
        self._dot = want
        if want:
            self.ctx.activity.show("media", dot=True, color=GREEN, persistent=True, priority=1)
        else:
            self.ctx.activity.clear("media")

    def _render_chips(self) -> None:
        choices = self.svc.choices()
        current = self.now.player if self.now else None
        key = tuple((c.player, c.source, c.player == current) for c in choices)
        if key == self._chips_key:
            return
        self._chips_key = key
        for child in self.chips.get_children():
            self.chips.remove(child)
            child.destroy()
        if len(choices) < 2:
            self.chips.hide()
            return
        for snap in choices[:4]:
            chip = _player_chip(snap, snap.player == current, lambda p=snap.player: self.svc.select(p))
            self.chips.pack_start(chip, False, False, 0)
            chip.show_all()
        self.chips.show()  # show_all() skips widgets marked no_show_all

    def _art_loaded(self, url: str, pixbuf) -> None:
        if url == self._art_url:
            self._art = pixbuf
            self.art.queue_draw()

    def _art_clicked(self, _w, event) -> bool:
        if event.button == 1 and self.now and self.now.can_raise:
            self.svc.raise_player()
            self.ctx.island.close()
        return True

    def _tick(self) -> None:
        now = self.now
        if not hasattr(self, "bar"):
            return
        self.bar.queue_draw()
        if now is None or now.length <= 0:
            self.elapsed.set_text("")
            self.remaining.set_text("")
            return
        position = self._drag * now.length if self._drag is not None else now.position_now()
        self.elapsed.set_text(fmt_time(position))
        self.remaining.set_text("-" + fmt_time(now.length - position))

    # -- progress bar ---------------------------------------------------------------
    def _frac_at(self, x: float) -> float:
        width = max(self.bar.get_allocated_width(), 1)
        return max(0.0, min(1.0, x / width))

    def _seekable(self) -> bool:
        return bool(self.now and self.now.can_seek and self.now.length > 0)

    def _bar_press(self, _w, event) -> bool:
        if event.button == 1 and self._seekable():
            self._drag = self._frac_at(event.x)
            self._tick()
        return True

    def _bar_motion(self, _w, event) -> bool:
        if self._drag is not None:
            self._drag = self._frac_at(event.x)
            self._tick()
        return True

    def _bar_release(self, _w, event) -> bool:
        if self._drag is not None and self.now:
            self.svc.seek_to(self._frac_at(event.x) * self.now.length)
        self._drag = None
        self._tick()
        return True

    def _bar_scroll(self, _w, event) -> bool:
        if not self._seekable():
            return False
        if event.direction == Gdk.ScrollDirection.SMOOTH:
            _ok, _dx, dy = event.get_scroll_deltas()
            if dy == 0:
                return True
            up = dy < 0
        else:
            up = event.direction in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.RIGHT)
        self.svc.seek_by(5 if up else -5)
        return True

    def _set_bar_hover(self, on: bool) -> bool:
        self._bar_hover = on
        self.bar.queue_draw()
        return False

    # -- drawing --------------------------------------------------------------------
    def _draw_art(self, widget, cr) -> bool:
        rounded_rect(cr, 0, 0, ART, ART, 10)
        cr.clip()
        if self._art is not None:
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
        from ..ui import theme

        now = self.now
        w, full_h = widget.get_allocated_width(), widget.get_allocated_height()
        h = 6 if (self._bar_hover or self._drag is not None) and self._seekable() else 4
        y = (full_h - h) / 2
        p = theme.current()
        pill(cr, 0, y, w, h)
        cr.set_source_rgba(*p.track)
        cr.fill()
        if now and now.length > 0:
            frac = self._drag if self._drag is not None else now.position_now() / now.length
            frac = max(0.0, min(1.0, frac))
            pill(cr, 0, y, max(h, w * frac), h)
            cr.set_source_rgba(*p.text[:3], 0.85)
            cr.fill()
        return True

    # -- lifecycle & commands -------------------------------------------------------
    def on_open(self) -> None:
        self.svc.refresh_position()

    def commands(self) -> list[Command]:
        def seek(arg: str) -> str | None:
            now = self.svc.current()
            if now is None or not now.can_seek:
                return "This player can't seek"
            target = parse_seek(arg, now.position_now())
            if target is None:
                return "seek +30 · seek -10 · seek 1:20"
            self.svc.seek_to(min(target, max(now.length - 1, 0)))
            return f"At {fmt_time(target)}"

        def shuffle(_a: str) -> str:
            on = self.svc.toggle_shuffle()
            return "This player has no shuffle" if on is None else f"Shuffle {'on' if on else 'off'}"

        def repeat(_a: str) -> str:
            loop = self.svc.cycle_loop()
            if loop is None:
                return "This player has no repeat"
            return {"None": "Repeat off", "Playlist": "Repeat all", "Track": "Repeat this track"}[loop]

        def player(arg: str) -> str:
            if not arg.strip():
                return ", ".join(s.source for s in self.svc.choices()) or "Nothing is playing"
            found = self.svc.select_by_name(arg)
            return f"Showing {found}" if found else f"No player matches {arg.strip()!r}"

        return [
            Command(("play", "pause"), "Play / pause", "play", lambda _a: self.svc.play_pause()),
            Command(("next",), "Next track", "next", lambda _a: self.svc.next()),
            Command(("prev", "previous"), "Previous track", "prev", lambda _a: self.svc.previous()),
            Command(("seek",), "Seek", "seek +30 · seek 1:20", seek, True),
            Command(("shuffle",), "Shuffle on / off", "shuffle", shuffle),
            Command(("repeat", "loop"), "Repeat: off / all / one", "repeat", repeat),
            Command(("player",), "Switch player", "player spotify", player, True),
        ]


def _set_dim(widget: Gtk.Widget, dim: bool) -> None:
    ctx = widget.get_style_context()
    (ctx.add_class if dim else ctx.remove_class)("cw-faded")


def _player_chip(snap, active: bool, on_click) -> Gtk.Button:
    button = Gtk.Button()
    button.set_relief(Gtk.ReliefStyle.NONE)
    button.set_can_focus(False)
    button.get_style_context().add_class("cw-chip")
    button.set_tooltip_text(f"{snap.source}: {snap.title}")
    box = Gtk.Box(spacing=5)
    icon = None
    if snap.app_icon:
        try:
            icon = Gio.Icon.new_for_string(snap.app_icon)
        except Exception:
            icon = None
    if icon is not None:
        image = Gtk.Image.new_from_gicon(icon, Gtk.IconSize.MENU)
        image.set_pixel_size(14)
        box.pack_start(image, False, False, 0)
    text = Gtk.Label(label=snap.site or snap.app or snap.identity.title())
    box.pack_start(text, False, False, 0)
    button.add(box)
    set_active(button, active)
    button.connect("clicked", lambda *_: on_click())
    return button


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
    elif url.startswith("data:image") and ";base64," in url:
        import base64

        path = str(cache / hashlib.sha1(url.encode()).hexdigest())
        try:
            open(path, "rb").close()
        except OSError:
            with open(path, "wb") as fh:
                fh.write(base64.b64decode(url.split(";base64,", 1)[1]))
    else:
        return None
    pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
    w, h = pixbuf.get_width(), pixbuf.get_height()
    scale = ART / min(w, h)
    return pixbuf.scale_simple(max(int(w * scale), ART), max(int(h * scale), ART),
                               GdkPixbuf.InterpType.BILINEAR)
