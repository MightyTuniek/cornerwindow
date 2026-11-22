"""Current weather from Open-Meteo (free, no API key). Off by default: add "weather" to a
column in [modules] and set [weather] latitude/longitude to enable it."""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

from gi.repository import Gtk

from ..ui.widgets import Card, label
from .base import Module, module

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


@module("weather", title="Weather", icon="weather")
class Weather(Module):
    def setup(self) -> None:
        self._fetched = 0.0

    def available(self) -> bool:
        return "latitude" in self.config and "longitude" in self.config

    def build(self):
        card = Card()
        row = Gtk.Box(spacing=10)
        self.icon = Gtk.Image.new_from_icon_name("weather-few-clouds-symbolic", Gtk.IconSize.DIALOG)
        self.icon.set_pixel_size(30)
        row.pack_start(self.icon, False, False, 0)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.temp = label("–", "cw-metric-value")
        self.desc = label(self.config.get("place", ""), "cw-metric-sub")
        texts.pack_start(self.temp, False, False, 0)
        texts.pack_start(self.desc, False, False, 0)
        row.pack_start(texts, True, True, 0)
        card.pack_start(row, False, False, 0)
        self.ctx.every(60.0, self.maybe_fetch)
        return card

    def maybe_fetch(self) -> None:
        if time.monotonic() - self._fetched < float(self.config.get("refresh_minutes", 15)) * 60:
            return
        self._fetched = time.monotonic()
        self.ctx.run_async(self._fetch, self._show)

    def _fetch(self) -> dict:
        unit = "fahrenheit" if self.config.get("units") == "imperial" else "celsius"
        query = urllib.parse.urlencode({
            "latitude": self.config["latitude"], "longitude": self.config["longitude"],
            "current": "temperature_2m,weather_code", "daily": "temperature_2m_max,temperature_2m_min",
            "temperature_unit": unit, "timezone": "auto", "forecast_days": 1,
        })
        with urllib.request.urlopen(f"https://api.open-meteo.com/v1/forecast?{query}", timeout=8) as resp:
            return json.loads(resp.read())

    def _show(self, data: dict) -> None:
        current = data.get("current", {})
        daily = data.get("daily", {})
        text, icon = CODES.get(int(current.get("weather_code", 0)), ("", "weather-few-clouds-symbolic"))
        self.icon.set_from_icon_name(icon, Gtk.IconSize.DIALOG)
        self.temp.set_text(f"{current.get('temperature_2m', 0):.0f}°")
        hi = (daily.get("temperature_2m_max") or [None])[0]
        lo = (daily.get("temperature_2m_min") or [None])[0]
        extra = f" · H {hi:.0f}° L {lo:.0f}°" if hi is not None and lo is not None else ""
        place = self.config.get("place", "")
        self.desc.set_text(f"{place + ' · ' if place else ''}{text}{extra}")
