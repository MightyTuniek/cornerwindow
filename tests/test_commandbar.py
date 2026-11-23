"""The command bar's pure parts: calculator extras, times and dates, input parsing, history, emoji."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from cornerwindow.core import calc, clock, cmdline, emoji
from cornerwindow.core.history import History
from cornerwindow.core.launcher import AppEntry, rank_apps

WARSAW = ZoneInfo("Europe/Warsaw")
NOW = dt.datetime(2026, 11, 23, 14, 30, tzinfo=WARSAW)  # a Monday


# ── calculator ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("expr, value", [
    ("15% of 80", 12), ("80 + 15%", 92), ("80 - 15%", 68), ("50%", 0.5), ("10 % 3", 1),
    ("1<<4", 16), ("0xff & 0x0f", 15), ("0b101 | 0b010", 7), ("~5", -6), ("2^10", 1024),
])
def test_calc_extras(expr, value):
    assert calc.looks_like_math(expr)
    assert calc.evaluate(expr) == pytest.approx(value)


def test_bit_operations_refuse_fractions_and_huge_shifts():
    with pytest.raises(ValueError):
        calc.evaluate("1.5 << 2")
    with pytest.raises(ValueError):
        calc.evaluate("1 << 100000")


@pytest.mark.parametrize("text, shown", [
    ("255 to hex", "0xff"), ("0xff to bin", "0b11111111"), ("0b1010 in dec", "10"), ("8 as oct", "0o10"),
    ("0xff", "255"), ("-10 to hex", "-0xa"),
])
def test_base_conversion(text, shown):
    assert calc.convert_base(text)[0] == shown


def test_base_conversion_ignores_other_text():
    assert calc.convert_base("12.5 to hex") is None
    assert calc.convert_base("5 km to mi") is None
    assert calc.convert_base("firefox") is None


@pytest.mark.parametrize("text, parsed", [
    ("100 usd to eur", (100.0, "USD", "EUR")), ("€20 in pln", (20.0, "EUR", "PLN")),
    ("50 $ to £", (50.0, "USD", "GBP")), ("1.000,50 eur to usd", (1000.5, "EUR", "USD")),
    ("1,000.50 EUR -> chf", (1000.5, "EUR", "CHF")), ("2,5 gbp as jpy", (2.5, "GBP", "JPY")),
])
def test_parse_currency(text, parsed):
    assert calc.parse_currency(text) == parsed


@pytest.mark.parametrize("text", ["5 km to mi", "10 cup to l", "100 usd", "usd to eur", "100 abc to eur"])
def test_parse_currency_rejects(text):
    assert calc.parse_currency(text) is None


def test_convert_currency_through_the_euro():
    rates = {"USD": 1.25, "PLN": 4.0}
    assert calc.convert_currency(125, "USD", "EUR", rates) == pytest.approx(100)
    assert calc.convert_currency(10, "EUR", "PLN", rates) == pytest.approx(40)
    assert calc.convert_currency(125, "USD", "PLN", rates) == pytest.approx(400)
    assert calc.convert_currency(1, "USD", "GBP", rates) is None
    assert calc.format_money(1234.5, "PLN") == "1 234.50 PLN"
    assert calc.format_money(1234.5, "JPY") == "1 234 JPY"


# ── times and dates ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text, hm", [
    ("7:30", (7, 30)), ("7pm", (19, 0)), ("7:30 pm", (19, 30)), ("12am", (0, 0)), ("12pm", (12, 0)),
    ("19:30", (19, 30)), ("1930", (19, 30)), ("noon", (12, 0)), ("midnight", (0, 0)), ("7", (7, 0)),
])
def test_parse_time_of_day(text, hm):
    assert clock.parse_time_of_day(text) == hm


@pytest.mark.parametrize("text", ["25:00", "13pm", "7:75", "abc", ""])
def test_parse_time_of_day_rejects(text):
    assert clock.parse_time_of_day(text) is None


def test_seconds_until_rolls_over_to_tomorrow():
    assert clock.seconds_until(15, 0, NOW) == 30 * 60
    assert clock.seconds_until(14, 0, NOW) == 23.5 * 3600


@pytest.mark.parametrize("text, seconds", [
    ("90m", 5400), ("1h30m", 5400), ("3 days", 259200), ("2 weeks 1 day", 1296000), ("1.5h", 5400),
])
def test_parse_duration(text, seconds):
    assert clock.parse_duration(text).total_seconds() == seconds


@pytest.mark.parametrize("text", ["", "soon", "5 parsecs", "3d later"])
def test_parse_duration_rejects(text):
    assert clock.parse_duration(text) is None


@pytest.mark.parametrize("text, date", [
    ("2026-12-25", dt.date(2026, 12, 25)), ("25.12.2026", dt.date(2026, 12, 25)),
    ("dec 25", dt.date(2026, 12, 25)), ("25 december", dt.date(2026, 12, 25)),
    ("march 1", dt.date(2027, 3, 1)), ("jan 5 2030", dt.date(2030, 1, 5)), ("tomorrow", dt.date(2026, 11, 24)),
    ("christmas", dt.date(2026, 12, 25)), ("new year", dt.date(2027, 1, 1)),
])
def test_parse_date(text, date):
    assert clock.parse_date(text, NOW.date()) == date


def test_find_zone():
    assert clock.find_zone("tokyo")[1].key == "Asia/Tokyo"
    assert clock.find_zone("New York")[1].key == "America/New_York"
    assert clock.find_zone("pst")[1].key == "America/Los_Angeles"
    assert clock.find_zone("km") is None
    assert clock.find_zone("10") is None


@pytest.mark.parametrize("text, starts", [
    ("time in tokyo", "22:30 Mon in Tokyo"),
    ("tokyo time", "22:30 Mon in Tokyo"),
    ("3pm in tokyo", "23:00 in Tokyo"),
    ("3pm london to tokyo", "00:00 in Tokyo (+1 day)"),
    ("now + 90m", "16:00"),
    ("in 3 days", "Thursday 26 November 2026 · in 3 days"),
    ("tomorrow + 2w", "Tuesday 8 December 2026 · in 15 days"),
    ("days until dec 25", "Friday 25 December 2026 · in 32 days (4 weeks 4 days)"),
    ("dec 25 - 10 days", "Tuesday 15 December 2026"),
    ("since 2026-11-20", "Friday 20 November 2026 · 3 days ago"),
    ("2026-12-25", "Friday 25 December 2026"),
    ("week", "Week 48"),
    ("today", "Monday 23 November 2026 · week 48"),
])
def test_clock_answers(text, starts):
    answer = clock.evaluate(text, NOW)
    assert answer is not None, text
    assert answer.text.startswith(starts), answer.text


@pytest.mark.parametrize("text", ["5 km to mi", "100 usd to eur", "firefox", "b 40", "12 - 3", "2*21"])
def test_clock_leaves_other_input_alone(text):
    assert clock.evaluate(text, NOW) is None


def test_epoch():
    assert clock.evaluate("unix", NOW).copy == str(int(NOW.timestamp()))
    assert clock.evaluate("@1700000000", NOW).copy.startswith("2023-11-14T23:13:20")


# ── input parsing ───────────────────────────────────────────────────────────────────────
def test_shell_line():
    assert cmdline.shell_line("> ls ~") == ("background", "ls ~")
    assert cmdline.shell_line(">>htop") == ("terminal", "htop")
    assert cmdline.shell_line("ls") is None


def test_terminal_argv():
    installed = {"kitty", "xterm"}
    which = lambda p: f"/usr/bin/{p}" if p in installed else None  # noqa: E731
    argv = cmdline.terminal_argv("htop", "", which)
    assert argv[:3] == ["kitty", "sh", "-c"] and argv[3].startswith("htop\n")
    assert cmdline.terminal_argv("htop", "xterm", which, keep_open=False) == ["xterm", "-e", "sh", "-c", "htop"]
    assert cmdline.terminal_argv("htop", "konsole", which) is None
    assert cmdline.terminal_argv("htop", "", lambda p: None) is None


@pytest.mark.parametrize("text, url", [
    ("github.com", "https://github.com"), ("github.com/foo/bar", "https://github.com/foo/bar"),
    ("www.example.xyzzy", "https://www.example.xyzzy"), ("https://a.b/c?d=1", "https://a.b/c?d=1"),
    ("localhost:8000", "http://localhost:8000"), ("notes.txt", None), ("5.5", None), ("hello world.com", None),
])
def test_as_url(text, url):
    assert cmdline.as_url(text) == url


def test_engine_query():
    engines = {"yt": "https://youtube.com/results?search_query={}", "g": "https://google.com/search?q="}
    assert cmdline.engine_query("yt lofi beats", engines) == \
        ("yt", "https://youtube.com/results?search_query=lofi+beats", "lofi beats")
    assert cmdline.engine_query("g a&b", engines)[1] == "https://google.com/search?q=a%26b"
    assert cmdline.engine_query("yt", engines) is None
    assert cmdline.engine_query("ytx lofi", engines) is None
    assert cmdline.site_name("https://www.youtube.com/results?x") == "youtube.com"


def test_complete_path(tmp_path):
    (tmp_path / "Documents").mkdir()
    (tmp_path / "Downloads").mkdir()
    (tmp_path / "dog.txt").write_text("")
    (tmp_path / ".hidden").mkdir()
    assert [i.path.name for i in cmdline.complete_path(str(tmp_path / "Dow"))] == ["Downloads"]
    names = [i.path.name for i in cmdline.complete_path(str(tmp_path / "d"))]
    assert names == ["Documents", "Downloads", "dog.txt"]
    assert [i.path.name for i in cmdline.complete_path(str(tmp_path) + "/.h")] == [".hidden"]
    listing = cmdline.complete_path(str(tmp_path) + "/")
    assert ".hidden" not in [i.path.name for i in listing] and listing[0].display.endswith("/")
    assert cmdline.looks_like_path("~/x") and cmdline.looks_like_path("/etc") and not cmdline.looks_like_path("x/y")


def test_rank_files():
    paths = ["/h/a/deep/x/report-final.pdf", "/h/report.txt", "/h/old/my report notes.md", "/h/reporter/z.png",
             "/h/nothing.txt"]
    ranked = cmdline.rank_files("report", paths, 10)
    assert ranked[0] == "/h/report.txt"
    assert "/h/nothing.txt" not in ranked
    assert ranked.index("/h/old/my report notes.md") > ranked.index("/h/a/deep/x/report-final.pdf")
    assert cmdline.rank_files("report pdf", paths) == ["/h/a/deep/x/report-final.pdf"]


# ── history & ranking ───────────────────────────────────────────────────────────────────
def test_history_recents_dedup_and_persist(tmp_path):
    path = tmp_path / "h.json"
    history = History(path)
    history.record("fire", "app:firefox.desktop", "Firefox", now=1000)
    history.record("b 40", "cmd:b", now=1001)
    history.record("firef", "app:firefox.desktop", "Firefox", now=1002)
    assert [e["line"] for e in history.recent()] == ["firef", "b 40"]
    assert history.lines() == ["firef", "b 40"]
    history.save()
    again = History(path)
    assert again.lines() == ["firef", "b 40"]
    assert again.picks["app:firefox.desktop"][0] == 2


def test_history_bonus_grows_with_use_and_fades():
    history = History()
    assert history.bonus("app:x") == 0
    history.record("x", "app:x", now=0)
    one = history.bonus("app:x", now=0)
    for _ in range(5):
        history.record("x", "app:x", now=0)
    assert history.bonus("app:x", now=0) > one
    assert history.bonus("app:x", now=86400 * 60) < history.bonus("app:x", now=0)
    assert history.bonus("app:x", now=0) <= 30


def test_rank_apps_bonus_reorders():
    entries = [AppEntry("Terminal"), AppEntry("Terminator")]
    assert [e.name for e in rank_apps("term", entries)] == ["Terminal", "Terminator"]
    boosted = rank_apps("term", entries, bonus=lambda e: 5.0 if e.name == "Terminator" else 0.0)
    assert boosted[0].name == "Terminator"


# ── emoji ───────────────────────────────────────────────────────────────────────────────
def test_emoji_search():
    entries = [("😀", "grinning face", ("smile", "happy")), ("❤", "red heart", ("love",)),
               ("💔", "broken heart", ("sad",)), ("→", "right arrow", ("right", "arrow"))]
    assert [e[0] for e in emoji.search("heart", entries)] == ["❤", "💔"]
    assert emoji.search("smile", entries)[0][0] == "😀"
    assert emoji.search("broken he", entries)[0][0] == "💔"
    assert emoji.search("zzz", entries) == []
    assert emoji.search(":", entries) == []


def test_bundled_symbols_are_searchable():
    found = [e[0] for e in emoji.search("degree")]
    assert "°" in found
