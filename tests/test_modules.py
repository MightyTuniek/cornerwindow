"""Module system tests (needs PyGObject, no display server)."""

from __future__ import annotations

import pytest

gi = pytest.importorskip("gi")

from cornerwindow.modules import registry  # noqa: E402
from cornerwindow.modules.base import Command, Module, module, registered  # noqa: E402
from cornerwindow.modules.timer import _fmt, _parse_duration  # noqa: E402


def test_decorator_registers():
    @module("test_dummy", title="Dummy")
    class Dummy(Module):
        def build(self):
            return None

    assert registered()["test_dummy"] is Dummy
    assert Dummy.title == "Dummy" and Dummy.area == "main"


def test_user_modules_load_from_folder(tmp_path):
    (tmp_path / "hello.py").write_text(
        "from cornerwindow.modules.base import Module, module\n"
        "@module('hello_user', title='Hello')\n"
        "class Hello(Module):\n"
        "    def build(self):\n"
        "        return None\n"
    )
    assert registry.load_user_modules([tmp_path]) == ["hello"]
    assert "hello_user" in registered()


def test_broken_user_module_does_not_crash(tmp_path):
    (tmp_path / "broken.py").write_text("raise RuntimeError('boom')\n")
    assert registry.load_user_modules([tmp_path]) == []


def test_command_matching():
    cmd = Command(("b", "brightness"), "Brightness")
    assert cmd.matches("b") and cmd.matches("bri") and not cmd.matches("x")


@pytest.mark.parametrize("text,seconds", [("10", 600), ("90s", 90), ("1h30m", 5400), ("1.5", 90)])
def test_timer_durations(text, seconds):
    assert _parse_duration(text) == seconds


def test_timer_format():
    assert _fmt(65) == "1:05"
    assert _fmt(3725) == "1:02:05"
    assert _parse_duration("soon") is None


def test_month_grid():
    import datetime as dt

    from cornerwindow.core.dates import month_grid, shift_month

    weeks = month_grid(2026, 11, 0)  # November 2026 starts on a Sunday
    assert len(weeks) == 6 and all(len(w) == 7 for w in weeks)
    assert weeks[0][0] == dt.date(2026, 10, 26) and weeks[0][6] == dt.date(2026, 11, 1)
    sunday_first = month_grid(2026, 11, 6)
    assert sunday_first[0][0] == dt.date(2026, 11, 1)
    assert shift_month(2026, 12, 1) == (2027, 1) and shift_month(2026, 1, -1) == (2025, 12)


def test_stopwatch():
    from cornerwindow.modules.stopwatch import Watch, fmt_elapsed

    now = [100.0]
    watch = Watch(clock=lambda: now[0])
    watch.toggle()
    now[0] = 103.5
    assert watch.lap() == 3.5
    now[0] = 110.0
    watch.toggle()  # pause at 10 s
    now[0] = 200.0
    assert watch.elapsed() == 10.0 and watch.lap() is None
    watch.toggle()
    now[0] = 202.0
    watch.lap()
    assert [(n, round(s, 1)) for n, s, _t in watch.splits()] == [(2, 8.5), (1, 3.5)]
    assert fmt_elapsed(62.34) == "1:02.3" and fmt_elapsed(3723) == "1:02:03"
    watch.reset()
    assert watch.elapsed() == 0 and not watch.laps


def test_weather_parsing():
    from cornerwindow.modules.weather import parse_forecast, parse_geocode

    data = {
        "current": {"time": "2026-11-23T01:00", "temperature_2m": 3.4, "weather_code": 3},
        "hourly": {"time": [f"2026-11-23T{h:02d}:00" for h in range(24)],
                   "temperature_2m": [float(h) for h in range(24)], "weather_code": [61] * 24},
        "daily": {"time": ["2026-11-23", "2026-11-24", "2026-11-25"], "weather_code": [3, 0, 71],
                  "temperature_2m_max": [5, 7, 1], "temperature_2m_min": [-1, 2, -4]},
    }
    info = parse_forecast(data, hours=3)
    assert info["temp"] == 3.4 and info["code"] == 3 and info["hi"] == 5 and info["lo"] == -1
    assert info["hours"] == [("02", 2.0, 61), ("03", 3.0, 61), ("04", 4.0, 61)]
    assert [d[1:] for d in info["days"]] == [(7, 2, 0), (1, -4, 71)]
    assert parse_forecast({})["hours"] == []
    places = parse_geocode({"results": [
        {"name": "Warsaw", "admin1": "Masovia", "country": "Poland", "latitude": 52.22977, "longitude": 21.01178},
        {"name": "Nowhere"},
    ]})
    assert places == [{"label": "Warsaw, Masovia, Poland", "place": "Warsaw", "latitude": 52.2298,
                       "longitude": 21.0118}]
