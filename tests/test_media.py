"""Media: telling the site behind a browser tab, player ranking, seek parsing, MPD parsing."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from cornerwindow.platform.media.model import NowPlaying, fmt_time, next_loop, parse_seek
from cornerwindow.platform.media.mpd import parse_response, snapshot_from
from cornerwindow.platform.media.service import matches, rank
from cornerwindow.platform.media.sites import clean_app_name, site_for, strip_site_suffix


@pytest.mark.parametrize("url,art,title,site", [
    ("https://www.youtube.com/watch?v=x", "", "", "YouTube"),
    ("https://music.youtube.com/watch?v=x", "", "", "YouTube Music"),
    ("https://soundcloud.com/a/b", "", "", "SoundCloud"),
    ("https://music.apple.com/us/album/x", "", "", "Apple Music"),
    ("https://open.spotify.com/track/x", "", "", "Spotify"),
    ("", "https://i.ytimg.com/vi/x/hqdefault.jpg", "", "YouTube"),
    ("", "https://i1.sndcdn.com/artworks-x.jpg", "", "SoundCloud"),
    ("", "https://is1-ssl.mzstatic.com/image/x.jpg", "", "Apple Music"),
    ("", "https://i.scdn.co/image/x", "", "Spotify"),
    ("", "file:///tmp/firefox-art.png", "Song - YouTube", "YouTube"),
    ("", "file:///tmp/x.png", "Just a song", ""),
    ("https://notyoutube.com/x", "", "", ""),
])
def test_site_for(url, art, title, site):
    assert site_for(url, art, title) == site


def test_title_suffix_and_app_names():
    assert strip_site_suffix("Song - YouTube") == "Song"
    assert strip_site_suffix("Song - YouTube Music") == "Song"
    assert strip_site_suffix("- YouTube") == "- YouTube"
    assert clean_app_name("Mozilla Firefox") == "Firefox"
    assert clean_app_name("VLC media player") == "VLC"
    assert clean_app_name("", "chromium") == "Chromium"
    assert clean_app_name("Mozilla zen") == "Zen"


def snap(**kw) -> NowPlaying:
    base = dict(player="p", identity="firefox", status="Playing", title="t", artist="", art_url="",
                length=200, position=10, position_time=0.0)
    base.update(kw)
    return NowPlaying(**base)


def test_source_label():
    assert snap(app="Firefox", site="YouTube").source == "YouTube · Firefox"
    assert snap(app="Spotify", site="Spotify").source == "Spotify"
    assert snap(app="", identity="vlc").source == "Vlc"


def test_seek_parsing_and_time():
    assert parse_seek("+30", 10) == 40
    assert parse_seek("-30", 10) == 0
    assert parse_seek("1:20", 0) == 80
    assert parse_seek("80", 5) == 80
    assert parse_seek("soon", 0) is None
    assert fmt_time(65) == "1:05" and fmt_time(3725) == "1:02:05"
    assert next_loop("None") == "Playlist" and next_loop("Track") == "None" and next_loop(None) == "Playlist"


def player(key, playing=False, last=0.0, names=""):
    return SimpleNamespace(key=key, playing=playing, last_active=last, names=lambda: names or key)


def test_rank_picked_then_playing_then_preferred_then_recent():
    a, b, c = player("a", last=5), player("b", playing=True), player("c", last=9, names="spotify")
    assert [p.key for p in rank([a, b, c], None, [])] == ["b", "c", "a"]
    assert [p.key for p in rank([a, b, c], "a", [])][0] == "a"
    a2, c2 = player("a", last=9), player("c", last=1, names="spotify")
    assert [p.key for p in rank([a2, c2], None, ["spotify"])] == ["c", "a"]
    assert matches("org.mpris.mediaplayer2.playerctld", ["playerctld"])


def test_mpd_parsing():
    text = "volume: 50\nrepeat: 1\nrandom: 0\nsingle: 1\nstate: play\nelapsed: 12.5\nduration: 200.0\n" \
           "list_OK\nfile: music/a.flac\nTitle: Song\nArtist: Band\nAlbum: LP\nlist_OK\n"
    status, song = parse_response(text)
    assert status["state"] == "play" and song["Title"] == "Song"
    now = snapshot_from(status, song, 0.0)
    assert now.playing and now.title == "Song" and now.loop == "Track" and now.shuffle is False
    assert now.length == 200 and now.can_seek
    stopped = snapshot_from(parse_response("state: stop\nlist_OK\nlist_OK\n")[0], {}, 0.0)
    assert stopped.status == "Stopped" and stopped.title == ""
    assert snapshot_from({"state": "pause"}, {"file": "x/radio.mp3"}, 0).title == "radio.mp3"
