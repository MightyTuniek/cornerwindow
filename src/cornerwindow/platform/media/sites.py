"""Which service a browser tab is playing (YouTube, SoundCloud, Apple Music...).

Browsers report themselves as the MPRIS player, so the site has to be guessed from
what the tab exposes: the page URL (when the browser gives one), the artwork host,
and the "- YouTube" style title suffix. Pure functions, covered by tests.
"""

from __future__ import annotations

import urllib.parse

# host suffix -> name. Longer suffixes are checked first, so music.youtube.com wins over youtube.com.
_PAGE_HOSTS = {
    "music.youtube.com": "YouTube Music",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "youtube-nocookie.com": "YouTube",
    "soundcloud.com": "SoundCloud",
    "open.spotify.com": "Spotify",
    "spotify.com": "Spotify",
    "music.apple.com": "Apple Music",
    "podcasts.apple.com": "Apple Podcasts",
    "deezer.com": "Deezer",
    "tidal.com": "Tidal",
    "bandcamp.com": "Bandcamp",
    "mixcloud.com": "Mixcloud",
    "twitch.tv": "Twitch",
    "vimeo.com": "Vimeo",
    "music.amazon.com": "Amazon Music",
    "music.amazon.co.uk": "Amazon Music",
    "music.amazon.de": "Amazon Music",
    "netflix.com": "Netflix",
    "primevideo.com": "Prime Video",
    "disneyplus.com": "Disney+",
    "max.com": "Max",
    "pandora.com": "Pandora",
    "audiomack.com": "Audiomack",
    "kick.com": "Kick",
    "dailymotion.com": "Dailymotion",
    "jellyfin.org": "Jellyfin",
    "plex.tv": "Plex",
}

# artwork CDNs -> name (browsers often give the cover URL even when they hide the page URL)
_ART_HOSTS = {
    "ytimg.com": "YouTube",
    "ggpht.com": "YouTube",
    "googleusercontent.com": "YouTube Music",
    "sndcdn.com": "SoundCloud",
    "scdn.co": "Spotify",
    "spotifycdn.com": "Spotify",
    "mzstatic.com": "Apple Music",
    "dzcdn.net": "Deezer",
    "resources.tidal.com": "Tidal",
    "bcbits.com": "Bandcamp",
    "mixcloud.com": "Mixcloud",
    "jtvnw.net": "Twitch",
    "vimeocdn.com": "Vimeo",
    "nflxso.net": "Netflix",
}

_TITLE_SUFFIXES = {
    " - YouTube Music": "YouTube Music",
    " - YouTube": "YouTube",
    " | Free Listening on SoundCloud": "SoundCloud",
    " - SoundCloud": "SoundCloud",
    " | Spotify": "Spotify",
    " - Twitch": "Twitch",
    " on Vimeo": "Vimeo",
    " | Netflix": "Netflix",
}


def _host(url: str) -> str:
    if not url or not url.startswith(("http://", "https://")):
        return ""
    try:
        return (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _match(host: str, table: dict[str, str]) -> str:
    if not host:
        return ""
    for suffix in sorted(table, key=len, reverse=True):
        if host == suffix or host.endswith("." + suffix):
            return table[suffix]
    return ""


def site_for(url: str = "", art_url: str = "", title: str = "") -> str:
    """The service behind a browser tab, or '' when it can't be told."""
    found = _match(_host(url), _PAGE_HOSTS)
    if found:
        return found
    found = _match(_host(art_url), _ART_HOSTS)
    if found:
        return found
    for suffix, name in _TITLE_SUFFIXES.items():
        if title.endswith(suffix):
            return name
    return ""


def strip_site_suffix(title: str) -> str:
    """'Song - YouTube' -> 'Song' (the site is shown separately)."""
    for suffix in sorted(_TITLE_SUFFIXES, key=len, reverse=True):
        if title.endswith(suffix) and len(title) > len(suffix):
            return title[: -len(suffix)]
    return title


_NOISE_PREFIXES = ("Mozilla ",)
_NOISE_SUFFIXES = (" media player", " Media Player", " Web Browser", " web browser")


def clean_app_name(identity: str, fallback: str = "") -> str:
    """'Mozilla Firefox' -> 'Firefox', 'VLC media player' -> 'VLC'."""
    name = (identity or "").strip()
    for prefix in _NOISE_PREFIXES:
        if name.startswith(prefix) and len(name) > len(prefix):
            name = name[len(prefix):]
    for suffix in _NOISE_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            name = name[: -len(suffix)]
    if not name:
        name = fallback.replace("_", " ").replace("-", " ").strip()
    if name.islower():
        name = name[:1].upper() + name[1:]
    return name
