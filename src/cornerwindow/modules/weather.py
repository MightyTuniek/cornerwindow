"""Weather from Open-Meteo (free, no API key): now, the next hours and the next days.

Pick your city in Settings → Weather (it looks the name up on Open-Meteo's geocoder only when
you press Find), or set [weather] latitude/longitude yourself.
"""

from __future__ import annotations

import datetime as dt
import json
import time
import urllib.parse
import urllib.request

from gi.repository import Gtk

from ..ui.widgets import Card, chip, label
from .base import Command, Module, module

CODES = {
    0: ("Clear", "weather-clear-symbolic"), 1: ("Mostly clear", "weather-few-clouds-symbolic"),
    2: ("Partly cloudy", "weather-few-clouds-symbolic"), 3: ("Overcast", "weather-overcast-symbolic"),
    45: ("Fog", "weather-fog-symbolic"), 48: ("Fog", "weather-fog-symbolic"),
    51: ("Drizzle", "weather-showers-scattered-symbolic"), 53: ("Drizzle", "weather-showers-scattered-symbolic"),
    55: ("Drizzle", "weather-showers-scattered-symbolic"), 61: ("Rain", "weather-showers-symbolic"),
    63: ("Rain", "weather-showers-symbolic"), 65: ("Heavy rain", "weather-showers-symbolic"),
    71: ("Snow", "weather-snow-symbolic"), 73: ("Snow", "weather-snow-symbolic"),
    75: ("Heavy snow", "weather-snow-symbolic"), 80: ("Showers", "weather-showers-symbolic"),
    81: ("Showers", "weather-showers-symbolic"), 82: ("Storm showers", "weather-storm-symbolic"),
    95: ("Thunderstorm", "weather-storm-symbolic"), 96: ("Thunderstorm", "weather-storm-symbolic"),
    99: ("Thunderstorm", "weather-storm-symbolic"),
}
FALLBACK = ("", "weather-few-clouds-symbolic")
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"


# -- pure helpers (tested) -----------------------------------------------------------------
def parse_forecast(data: dict, hours: int = 6, days: int = 5) -> dict:
    """Open-Meteo JSON -> {temp, code, hi, lo, hours: [(label, temp, code)], days: [(label, hi, lo, code)]}."""
    current = data.get("current") or {}
    daily = data.get("daily") or {}
    hourly = data.get("hourly") or {}
    out = {"temp": current.get("temperature_2m"), "code": int(current.get("weather_code", 0) or 0),
           "hi": _first(daily.get("temperature_2m_max")), "lo": _first(daily.get("temperature_2m_min")),
           "hours": [], "days": []}
    now = str(current.get("time", ""))
    times = hourly.get("time") or []
    start = next((i for i, t in enumerate(times) if t > now), len(times))
    for i in range(start, min(start + hours, len(times))):
        out["hours"].append((times[i][11:13] if len(times[i]) >= 13 else times[i],
                             _at(hourly.get("temperature_2m"), i), int(_at(hourly.get("weather_code"), i) or 0)))
    dates = daily.get("time") or []
    for i in range(1, min(days + 1, len(dates))):  # tomorrow onwards; today is in the headline
        try:
            name = dt.date.fromisoformat(dates[i]).strftime("%a")
        except ValueError:
            name = dates[i]
        out["days"].append((name, _at(daily.get("temperature_2m_max"), i), _at(daily.get("temperature_2m_min"), i),
                            int(_at(daily.get("weather_code"), i) or 0)))
    return out


def parse_geocode(data: dict) -> list[dict]:
    """Geocoder JSON -> [{label, place, latitude, longitude}]."""
    out = []
    for item in data.get("results") or []:
        name = item.get("name")
        if not name or item.get("latitude") is None or item.get("longitude") is None:
            continue
        where = ", ".join(str(x) for x in (item.get("admin1"), item.get("country")) if x and x != name)
        out.append({"label": f"{name}, {where}" if where else name, "place": name,
                    "latitude": round(float(item["latitude"]), 4), "longitude": round(float(item["longitude"]), 4)})
    return out


def geocode(name: str, language: str = "en") -> list[dict]:
    """Blocking: look a place name up on Open-Meteo's geocoder."""
    query = urllib.parse.urlencode({"name": name, "count": 6, "language": language, "format": "json"})
    with urllib.request.urlopen(f"{GEOCODE_URL}?{query}", timeout=8) as resp:  # noqa: S310 (fixed host)
        return parse_geocode(json.loads(resp.read()))


def _first(values):
    return values[0] if values else None


def _at(values, i):
    return values[i] if values and i < len(values) else None


def _deg(value) -> str:
    return "–" if value is None else f"{value:.0f}°"


# -- the card ------------------------------------------------------------------------------
@module("weather", title="Weather", icon="weather", description="Current weather and forecast from Open-Meteo")
class Weather(Module):
    def setup(self) -> None:
        self._fetched = 0.0
        self._data: dict | None = None

    def configure(self) -> None:
        key = (self.config.get("latitude"), self.config.get("longitude"), self.config.get("units"))
        if key != getattr(self, "_key", None):
            self._key, self._fetched, self._data = key, 0.0, None  # new place: fetch again

    def located(self) -> bool:
        return self.config.get("latitude") is not None and self.config.get("longitude") is not None

    def build(self):
        card = Card()
        if not self.located():
            card.pack_start(label("Weather", "cw-title"), False, False, 0)
            card.pack_start(label("Pick your city to see the weather.", "cw-dim cw-small"), False, False, 0)
            button = chip("Choose city…", lambda: self.ctx.actions.run("settings.page", page="weather"))
            button.set_halign(Gtk.Align.START)
            card.pack_start(button, False, False, 0)
            return card
        row = Gtk.Box(spacing=10)
        self.icon = Gtk.Image.new_from_icon_name(FALLBACK[1], Gtk.IconSize.DIALOG)
        self.icon.set_pixel_size(30)
        row.pack_start(self.icon, False, False, 0)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.temp = label("–", "cw-metric-value")
        self.desc = label(self.config.get("place", ""), "cw-metric-sub", ellipsize=True)
        texts.pack_start(self.temp, False, False, 0)
        texts.pack_start(self.desc, False, False, 0)
        row.pack_start(texts, True, True, 0)
        card.pack_start(row, False, False, 0)
        mode = str(self.config.get("forecast", "hours"))
        self.hours_row = _strip() if mode in ("hours", "both") else None
        self.days_row = _strip() if mode in ("days", "both") else None
        for strip in (self.hours_row, self.days_row):
            if strip is not None:
                card.pack_start(strip, False, False, 0)
        self.ctx.every(60.0, self.maybe_fetch)
        if self._data is not None:
            self._show(self._data)
        return card

    def maybe_fetch(self) -> None:
        if not self.located():
            return
        if time.monotonic() - self._fetched < float(self.config.get("refresh_minutes", 15)) * 60:
            return
        self._fetched = time.monotonic()
        self.ctx.run_async(self._fetch, self._show)

    def _fetch(self) -> dict:
        unit = "fahrenheit" if self.config.get("units") == "imperial" else "celsius"
        query = urllib.parse.urlencode({
            "latitude": self.config["latitude"], "longitude": self.config["longitude"],
            "current": "temperature_2m,weather_code",
            "hourly": "temperature_2m,weather_code",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min",
            "temperature_unit": unit, "timezone": "auto", "forecast_days": 6,
        })
        with urllib.request.urlopen(f"{FORECAST_URL}?{query}", timeout=8) as resp:  # noqa: S310 (fixed host)
            return json.loads(resp.read())

    def _show(self, data: dict) -> None:
        self._data = data
        if not hasattr(self, "temp"):
            return
        info = parse_forecast(data)
        text, icon = CODES.get(info["code"], FALLBACK)
        self.icon.set_from_icon_name(icon, Gtk.IconSize.DIALOG)
        self.temp.set_text(_deg(info["temp"]))
        extra = f" · H {_deg(info['hi'])} L {_deg(info['lo'])}" if info["hi"] is not None else ""
        place = self.config.get("place", "")
        self.desc.set_text(f"{place + ' · ' if place else ''}{text}{extra}")
        if self.hours_row is not None:
            _fill(self.hours_row, [(h, _deg(t), c) for h, t, c in info["hours"]])
        if self.days_row is not None:
            _fill(self.days_row, [(d, f"{_deg(hi)} {_deg(lo)}", c) for d, hi, lo, c in info["days"]])

    def commands(self) -> list[Command]:
        def run(_a: str) -> str:
            if not self.located():
                self.ctx.actions.run("settings.page", page="weather")
                return "Pick your city in settings"
            if self._data is None:
                return "Weather not loaded yet"
            info = parse_forecast(self._data)
            return f"{_deg(info['temp'])} {CODES.get(info['code'], FALLBACK)[0]}"

        return [Command(("weather",), "Weather now", "weather", run)]


def _strip() -> Gtk.Box:
    box = Gtk.Box(spacing=0, homogeneous=True)
    box.get_style_context().add_class("cw-forecast")
    return box


def _fill(box: Gtk.Box, items: list[tuple[str, str, int]]) -> None:
    for child in box.get_children():
        box.remove(child)
        child.destroy()
    for top, bottom, code in items:
        cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        cell.pack_start(label(top, "cw-dim cw-small", xalign=0.5), False, False, 0)
        image = Gtk.Image.new_from_icon_name(CODES.get(code, FALLBACK)[1], Gtk.IconSize.MENU)
        image.set_pixel_size(16)
        cell.pack_start(image, False, False, 0)
        cell.pack_start(label(bottom, "cw-small cw-tnum", xalign=0.5), False, False, 0)
        box.pack_start(cell, True, True, 0)
    box.show_all()
