"""The pure logic behind the newer cards and tools: notifications, downloads, calendar, window
layouts, snippets, alerts, processes and ports, updates, capture, battery, toggles."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

UTC = dt.timezone.utc


# -- notifications -------------------------------------------------------------------------
def test_notification_parsing_and_history():
    from cornerwindow.platform.notifications import History, ago, clean, from_notify

    item = from_notify(("Firefox", 0, "firefox", "Download <b>done</b>", "report.pdf &amp; more", [],
                        {"desktop-entry": "firefox", "urgency": 2}, -1), at=100.0)
    assert (item.app, item.summary, item.body, item.desktop_entry, item.urgent) == \
        ("Firefox", "Download done", "report.pdf & more", "firefox", True)
    assert from_notify(("x", 0, "", "", "", [], {}, -1)) is None  # nothing to show
    assert from_notify(("bad",)) is None
    assert clean("  a\n  <i>b</i> ") == "a b"
    # a body with no summary becomes the summary
    only_body = from_notify(("App", 0, "", "", "Hello", [], {}, -1))
    assert (only_body.summary, only_body.body) == ("Hello", "")

    history = History(size=2)
    for summary in ("one", "two", "two", "three"):
        history.add(from_notify(("Chat", 0, "", summary, "", [], {}, -1)))
    assert [n.summary for n in history.items] == ["three", "two"]  # resent "two" replaced, capped at 2
    assert history.unread == 4
    history.remove(history.items[0].id)
    assert [n.summary for n in history.items] == ["two"]
    history.seen()
    assert history.unread == 0
    assert (ago(5), ago(120), ago(7200), ago(200000)) == ("now", "2 min", "2 h", "2 d")


# -- downloads -----------------------------------------------------------------------------
def test_download_names():
    from cornerwindow.platform.downloads import final_name, human_size

    d = Path("/dl")
    assert final_name(d / "x.zip.part") == d / "x.zip"
    assert final_name(d / "x.zip.Ab12cD.part") == d / "x.zip"  # Firefox's random infix
    assert final_name(d / "movie.mkv.crdownload") == d / "movie.mkv"
    assert final_name(d / "notes.txt") is None
    assert final_name(d / ".part") is None
    assert human_size(512) == "512 B" and human_size(3 * 1024 * 1024) == "3.0 MB"


def test_download_tracker_lifecycle():
    from cornerwindow.platform.downloads import Tracker

    d = Path("/dl")
    t = Tracker(keep_seconds=60)
    assert t.changed(d / "big.iso.part", 1000, now=0)
    assert not t.changed(d / "big.iso.part", 2000, now=1)  # a few KB more: no redraw
    assert t.changed(d / "big.iso.part", 5_000_000, now=2)
    assert not t.created(d / "big.iso", 0, now=2)  # the browser's empty placeholder
    assert [x.name for x in t.active()] == ["big.iso"] and t.active()[0].size == 5_000_000
    assert t.done(d / "big.iso.part", final_exists=True, size=6_000_000, now=3)
    assert t.active() == [] and t.recent()[0].size == 6_000_000
    t.prune(now=100)
    assert t.recent() == []  # finished long enough ago
    # cancelled: the partial file disappears without a final file
    t.changed(d / "a.bin.part", 10, now=0)
    assert t.done(d / "a.bin.part", final_exists=False, now=1) and t.items == {}
    # saved directly, no partial file
    assert t.created(d / "photo.jpg", 1234, now=5) and t.recent()[0].name == "photo.jpg"
    assert not t.changed(d / ".hidden.part", 10)


# -- calendar ------------------------------------------------------------------------------
ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:standup
SUMMARY:Standup
DTSTART;TZID=Europe/Warsaw:20261123T093000
DTEND;TZID=Europe/Warsaw:20261123T094500
RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR;COUNT=6
EXDATE;TZID=Europe/Warsaw:20261125T093000
BEGIN:VALARM
SUMMARY:not the event
END:VALARM
END:VEVENT
BEGIN:VEVENT
UID:standup
RECURRENCE-ID;TZID=Europe/Warsaw:20261127T093000
SUMMARY:Standup (moved)
DTSTART;TZID=Europe/Warsaw:20261127T110000
DTEND;TZID=Europe/Warsaw:20261127T111500
END:VEVENT
BEGIN:VEVENT
SUMMARY:Holiday\\, finally
DTSTART;VALUE=DATE:20261124
DTEND;VALUE=DATE:20261126
END:VEVENT
BEGIN:VEVENT
SUMMARY:Lunch with
  Anna
DTSTART:20261123T120000Z
DURATION:PT1H
END:VEVENT
BEGIN:VEVENT
SUMMARY:Cancelled
STATUS:CANCELLED
DTSTART:20261123T150000Z
END:VEVENT
END:VCALENDAR
"""


def test_ics_parsing_and_recurrence():
    from cornerwindow.platform.calendar import occurrences, parse_ics

    waw = ZoneInfo("Europe/Warsaw")
    events = parse_ics(ICS, local=waw)
    assert sorted(e.summary for e in events) == ["Holiday, finally", "Lunch with Anna", "Standup", "Standup (moved)"]
    lunch = next(e for e in events if e.summary.startswith("Lunch"))
    assert lunch.end - lunch.start == dt.timedelta(hours=1) and lunch.start.tzinfo == UTC
    holiday = next(e for e in events if e.summary.startswith("Holiday"))
    assert holiday.all_day and (holiday.end - holiday.start).days == 2

    start = dt.datetime(2026, 11, 23, tzinfo=waw)
    found = occurrences(events, start, start + dt.timedelta(days=14))
    standups = [o.start.strftime("%a %d %H:%M") for o in found if o.summary.startswith("Standup")]
    # Mon 23, (Wed 25 excluded), Fri 27 moved to 11:00, Mon 30, Wed 2, Fri 4 -> COUNT=6 incl. the excluded one
    assert standups == ["Mon 23 09:30", "Fri 27 11:00", "Mon 30 09:30", "Wed 02 09:30", "Fri 04 09:30"]


def test_next_event_text():
    from cornerwindow.platform.calendar import Occurrence, format_next

    now = dt.datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
    at = lambda h, m, day=23: dt.datetime(2026, 11, day, h, m, tzinfo=UTC)  # noqa: E731
    assert format_next(Occurrence("Standup", at(9, 25), at(9, 40)), now) == "Standup · in 25 min"
    assert format_next(Occurrence("Call", at(8, 50), at(9, 30)), now) == "Call · now"
    assert format_next(Occurrence("Review", at(14, 0), at(15, 0)), now) == "Review · 14:00"
    assert format_next(Occurrence("Gym", at(7, 5, 24), at(8, 0, 24)), now) == "Gym · tomorrow 7:05"
    assert format_next(Occurrence("Trip", at(0, 0, 26), at(0, 0, 27), all_day=True), now) == "Trip · Thu"


def test_evolution_cache_is_read(tmp_path):
    import sqlite3

    from cornerwindow.platform.calendar import evolution_texts, parse_ics

    folder = tmp_path / ".cache/evolution/calendar/abc"
    folder.mkdir(parents=True)
    with sqlite3.connect(folder / "cache.db") as db:
        db.execute("CREATE TABLE ECacheObjects (ECacheUID TEXT, ECacheOBJ TEXT)")
        db.execute("INSERT INTO ECacheObjects VALUES (?, ?)",
                   ("1", "BEGIN:VEVENT\nSUMMARY:From Google\nDTSTART:20261201T100000Z\nEND:VEVENT"))
    local = tmp_path / ".local/share/evolution/calendar/system"
    local.mkdir(parents=True)
    (local / "calendar.ics").write_text("BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:Local\nDTSTART:20261201T100000Z\n"
                                        "END:VEVENT\nEND:VCALENDAR\n")
    summaries = sorted(e.summary for text in evolution_texts(tmp_path) for e in parse_ics(text))
    assert summaries == ["From Google", "Local"]


# -- window layouts ------------------------------------------------------------------------
def test_snap_geometry():
    from cornerwindow.core.snap import centered, contains, layout_rect, resolve, to_monitor

    work = (0, 32, 1920, 1048)
    assert layout_rect("left", work) == (0, 32, 960, 1048)
    assert layout_rect("bottom-right", work) == (960, 556, 960, 524)
    assert layout_rect("left", work, gap=10) == (10, 42, 945, 1028)  # 10 outside, 5 towards the middle
    assert layout_rect("right-third", work)[0] == 1280
    assert (resolve("tl"), resolve("max"), resolve("NEXT"), resolve("top-r"), resolve("nope")) == \
        ("top-left", "max", "next-monitor", "top-right", None)
    assert resolve("top") == "top"  # exact wins over the top-left/top-right prefixes
    assert centered((0, 0, 800, 600), work) == (560, 256, 800, 600)
    assert centered((0, 0, 1920, 1040), work)[2] == round(1920 * 0.7)  # too big: shrunk
    moved = to_monitor((100, 132, 960, 524), work, (1920, 0, 2560, 1440))
    assert moved[0] >= 1920 and moved[2] == 1280
    assert contains(work, 5, 40) and not contains(work, 5, 10)


# -- snippets ------------------------------------------------------------------------------
def test_snippet_expansion():
    from cornerwindow.core.snippets import expand, matching, needs_clipboard

    now = dt.datetime(2026, 11, 23, 14, 5)
    assert expand("{date} {time} {weekday}", now) == "2026-11-23 14:05 Monday"
    assert expand("{date:%d.%m.%Y} · {{literal}}", now) == "23.11.2026 · {literal}"
    assert expand("Re: {clipboard}", now, clipboard="hello") == "Re: hello"
    assert expand("{unknown}", now) == "{unknown}"
    assert needs_clipboard("x {clipboard}") and not needs_clipboard("{date}")
    items = {"sig": "Kind regards", "addr": "Main St 1", "sign": "x"}
    assert [k for k, _v in matching(items, "sig")] == ["sig", "sign"]
    assert [k for k, _v in matching(items, "main")] == ["addr"]
    assert len(matching(items, "")) == 3


# -- alerts --------------------------------------------------------------------------------
def test_alert_rules_sustain_and_cooldown():
    from cornerwindow.core.alerts import Rules

    rules = Rules(cpu=90, sustain=60, cooldown=600, disk_gb=5)
    assert rules.check(0, cpu=95) == []
    assert rules.check(30, cpu=95) == []
    assert [a[0] for a in rules.check(61, cpu=95)] == ["cpu"]
    assert rules.check(120, cpu=95) == []  # cooling down
    assert rules.check(130, cpu=20) == []  # condition ended: the timer resets
    assert rules.check(700, cpu=95) == [] and [a[0] for a in rules.check(761, cpu=95)] == ["cpu"]
    assert [a[0] for a in rules.check(0, disk_free_gb=2)] == ["disk"]  # disk needs no sustain
    off = Rules.from_section({"cpu": 0})
    assert off.check(0, cpu=100) == [] and off.check(1000, cpu=100) == []


# -- processes and ports -------------------------------------------------------------------
def test_proc_parsers():
    from cornerwindow.platform.processes import Proc, find, parse_net, parse_stat, parse_statm_rss, rank

    stat = "1234 (Web Content (x)) S 1 2 3 4 5 6 7 8 9 10 250 50 0 0 20 0"
    assert parse_stat(stat) == ("Web Content (x)", 300)
    assert parse_statm_rss("1000 256 0 0 0 0 0") == 1.0
    tcp = ("  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
           "   0: 0100007F:0BB8 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 4242 1\n"
           "   1: 0100007F:1F90 0100007F:9C40 01 00000000:00000000 00:00000000 00000000  1000        0 5151 1\n")
    (listener,) = parse_net(tcp, "tcp")
    assert (listener.port, listener.inode, listener.address) == (3000, 4242, "127.0.0.1")
    procs = [Proc(1, "firefox", 3.0, 900), Proc(2, "code", 40.0, 500), Proc(3, "idle", 0.0, 10)]
    assert [p.name for p in rank(procs, 2)] == ["code", "firefox"]
    assert [p.pid for p in find("fire", procs)] == [1]
    assert [p.pid for p in find("code", procs + [Proc(4, "codehelper")])] == [2]  # exact name wins


# -- updates -------------------------------------------------------------------------------
def test_update_parsers():
    from cornerwindow.platform.updates import Updates, parse_apt_check, parse_apt_list, parse_dnf, parse_lines

    assert parse_apt_check("12;3") == (12, 3) and parse_apt_check("junk") == (0, 0)
    apt = "Listing... Done\nfirefox/noble-updates 133.0 amd64 [upgradable from: 132.0]\nvim/noble 2:9 amd64 [upgradable from: 2:8]\n"
    assert parse_apt_list(apt) == ["firefox", "vim"]
    dnf = "Last metadata expiration check: 0:01:02 ago\nkernel.x86_64   6.11.4-301.fc41   updates\nvim-enhanced.x86_64  2:9.1-1.fc41  updates\n"
    assert parse_dnf(dnf) == ["kernel", "vim-enhanced"]
    assert parse_lines("org.gimp.GIMP\norg.mozilla.firefox\n") == ["org.gimp.GIMP", "org.mozilla.firefox"]
    found = Updates(system=12, security=3, flatpak=2)
    assert found.total == 14 and found.summary() == "12 system (3 security) · 2 Flatpak"
    assert Updates().summary() == "Up to date"


# -- capture -------------------------------------------------------------------------------
def test_color_formats_and_ocr_cleanup():
    from cornerwindow.platform.capture import clean_ocr, formats, parse_color

    assert formats(1, 0.5, 0) == {"hex": "#ff8000", "rgb": "rgb(255, 128, 0)", "hsl": "hsl(30, 100%, 50%)"}
    assert parse_color("#1A2b3C\n") == (0x1A / 255, 0x2B / 255, 0x3C / 255)
    assert parse_color("rgb(255, 0, 10)") == (1.0, 0.0, 10 / 255)
    assert parse_color("nothing") is None
    assert clean_ocr("Hello\nworld\n\n  Second   para\f") == "Hello world\n\nSecond para"


# -- small decluttering rules --------------------------------------------------------------
def test_battery_shows_peripherals_only_when_low():
    from types import SimpleNamespace as D

    from cornerwindow.modules.battery import shown_devices

    laptop = D(is_laptop=True, kind="battery", percent=80)
    mouse = D(is_laptop=False, kind="mouse", percent=72)
    pad = D(is_laptop=False, kind="gamepad", percent=12)
    devices = [laptop, mouse, pad]
    assert shown_devices(devices, "low", 20) == [laptop, pad]
    assert shown_devices(devices, "always", 20) == devices
    assert shown_devices(devices, "never", 20) == [laptop]
    assert shown_devices(devices, True, 20) == devices and shown_devices(devices, False, 20) == [laptop]


def test_toggles_overflow_behind_more():
    from cornerwindow.modules.toggles import overflow_split

    assert overflow_split(8, 4, 2) == 8  # fits exactly: no More tile
    assert overflow_split(9, 4, 2) == 7  # 7 tiles + More
    assert overflow_split(20, 4, 0) == 20  # no limit


def test_pw_dump_links_streams_to_sinks():
    from cornerwindow.platform.audio.base import parse_pw_dump, pw_serials

    objects = [
        {"id": 50, "info": {"props": {"media.class": "Audio/Sink", "object.serial": 77}}},
        {"id": 51, "info": {"props": {"media.class": "Audio/Sink", "object.serial": 78}}},
        {"id": 60, "info": {"props": {"media.class": "Stream/Output/Audio", "application.name": "Spotify"},
                            "params": {"Props": [{"channelVolumes": [1.0], "mute": False}]}}},
        {"id": 90, "type": "PipeWire:Interface:Link", "info": {"output-node-id": 60, "input-node-id": 51}},
    ]
    (stream,) = parse_pw_dump(json.dumps(objects))
    assert stream.sink == "51"
    assert pw_serials(json.dumps(objects)) == {"50": "77", "51": "78"}
