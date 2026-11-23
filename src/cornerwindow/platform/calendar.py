"""Upcoming events, for the clock card ("Next: Standup · 11:30") and the month grid's dots.

Sources, all read-only and without extra packages:
  * Evolution Data Server's files: local calendars (calendar.ics) and the caches of online
    accounts (cache.db, SQLite holding iCalendar text). GNOME Calendar and GNOME Online Accounts
    (Google, Nextcloud, Exchange) all end up there.
  * `.ics` files or URLs from `[calendar] sources` (a Google "secret address", a Nextcloud link).

`parse_ics` and `occurrences` are pure, for the tests. Recurrence covers what calendars
actually send: DAILY / WEEKLY (BYDAY) / MONTHLY / YEARLY with INTERVAL, COUNT, UNTIL, EXDATE.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from ..core import log, tasks

_log = log.get("calendar")
DAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


@dataclass
class Event:
    summary: str
    start: dt.datetime  # aware (local time for all-day events)
    end: dt.datetime
    all_day: bool = False
    location: str = ""
    rrule: dict = field(default_factory=dict)
    exdates: set = field(default_factory=set)
    uid: str = ""
    recurrence_id: dt.datetime | None = None


@dataclass
class Occurrence:
    summary: str
    start: dt.datetime
    end: dt.datetime
    all_day: bool = False
    location: str = ""


# -- parsing -------------------------------------------------------------------------------
def unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def _split(line: str) -> tuple[str, dict[str, str], str]:
    head, _, value = line.partition(":")
    name, *params = head.split(";")
    out = {}
    for param in params:
        key, _, val = param.partition("=")
        out[key.upper()] = val.strip('"')
    return name.upper(), out, value


def parse_time(value: str, params: dict[str, str], local: dt.tzinfo) -> tuple[dt.datetime, bool]:
    """-> (aware datetime, is a whole day)."""
    value = value.strip()
    if params.get("VALUE") == "DATE" or (len(value) == 8 and value.isdigit()):
        day = dt.datetime.strptime(value[:8], "%Y%m%d")
        return day.replace(tzinfo=local), True
    utc = value.endswith("Z")
    moment = dt.datetime.strptime(value.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
    if utc:
        return moment.replace(tzinfo=dt.timezone.utc), False
    zone = params.get("TZID")
    if zone:
        try:
            return moment.replace(tzinfo=ZoneInfo(zone.strip("/"))), False
        except Exception:
            pass  # a Windows zone name or a custom VTIMEZONE: treat it as local
    return moment.replace(tzinfo=local), False


def _duration(value: str) -> dt.timedelta:
    sign = -1 if value.startswith("-") else 1
    value = value.lstrip("+-").lstrip("P")
    total = dt.timedelta()
    number = ""
    in_time = False
    for ch in value:
        if ch == "T":
            in_time = True
        elif ch.isdigit():
            number += ch
        else:
            n = int(number or 0)
            number = ""
            total += {"W": dt.timedelta(weeks=n), "D": dt.timedelta(days=n),
                      "H": dt.timedelta(hours=n), "S": dt.timedelta(seconds=n)}.get(
                ch, dt.timedelta(minutes=n) if ch == "M" and in_time else dt.timedelta())
    return sign * total


def parse_ics(text: str, local: dt.tzinfo | None = None) -> list[Event]:
    local = local or dt.datetime.now().astimezone().tzinfo
    events: list[Event] = []
    current: dict | None = None
    depth = 0  # VALARMs nest inside VEVENTs
    for line in unfold(text):
        name, params, value = _split(line)
        if name == "BEGIN" and value.upper() == "VEVENT":
            current, depth = {"exdates": set()}, 0
            continue
        if current is None:
            continue
        if name == "BEGIN":
            depth += 1
            continue
        if name == "END" and value.upper() != "VEVENT":
            depth -= 1
            continue
        if name == "END":
            event = _finish(current)
            if event is not None:
                events.append(event)
            current = None
            continue
        if depth:
            continue
        try:
            if name == "SUMMARY":
                current["summary"] = _unescape(value)
            elif name == "LOCATION":
                current["location"] = _unescape(value)
            elif name == "DTSTART":
                current["start"] = parse_time(value, params, local)
            elif name == "DTEND":
                current["end"] = parse_time(value, params, local)[0]
            elif name == "DURATION":
                current["duration"] = _duration(value)
            elif name == "RRULE":
                current["rrule"] = {k.upper(): v for k, _, v in (p.partition("=") for p in value.split(";")) if k}
            elif name == "EXDATE":
                for part in value.split(","):
                    current["exdates"].add(parse_time(part, params, local)[0])
            elif name == "STATUS":
                current["cancelled"] = value.strip().upper() == "CANCELLED"
            elif name == "UID":
                current["uid"] = value.strip()
            elif name == "RECURRENCE-ID":
                current["recurrence_id"] = parse_time(value, params, local)[0]
        except (ValueError, IndexError):
            _log.debug("Skipping %s:%s", name, value)
    return events


def _finish(data: dict) -> Event | None:
    if data.get("cancelled") or "start" not in data:
        return None
    start, all_day = data["start"]
    end = data.get("end") or (start + data["duration"] if "duration" in data else
                              start + (dt.timedelta(days=1) if all_day else dt.timedelta()))
    return Event(data.get("summary", "Event"), start, end, all_day, data.get("location", ""),
                 data.get("rrule", {}), data["exdates"], data.get("uid", ""), data.get("recurrence_id"))


def _unescape(value: str) -> str:
    return value.replace("\\n", " ").replace("\\N", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


# -- recurrence ----------------------------------------------------------------------------
def occurrences(events: list[Event], start: dt.datetime, end: dt.datetime, limit: int = 500) -> list[Occurrence]:
    """Every occurrence overlapping [start, end), soonest first. A moved instance (RECURRENCE-ID)
    replaces the one it was moved from."""
    moved = {(e.uid, e.recurrence_id) for e in events if e.recurrence_id is not None}
    out: list[Occurrence] = []
    for event in events:
        length = event.end - event.start
        for when in _starts(event, end):
            if when + length <= start:
                continue
            if event.recurrence_id is None and (event.uid, when) in moved:
                continue
            out.append(Occurrence(event.summary, when, when + length, event.all_day, event.location))
            if len(out) >= limit:
                break
    return sorted(out, key=lambda o: (o.start, o.summary))


def _starts(event: Event, until: dt.datetime):
    rule = event.rrule
    if not rule:
        if event.start < until:
            yield event.start
        return
    freq = rule.get("FREQ", "").upper()
    interval = max(1, int(rule.get("INTERVAL", "1") or 1))
    count = int(rule["COUNT"]) if rule.get("COUNT", "").isdigit() else None
    rule_end = until
    if rule.get("UNTIL"):
        try:
            rule_end = min(until, parse_time(rule["UNTIL"], {}, event.start.tzinfo)[0] + dt.timedelta(seconds=1))
        except ValueError:
            pass
    by_day = [DAYS[d[-2:]] for d in rule.get("BYDAY", "").split(",") if d[-2:] in DAYS]
    produced = 0
    steps = 0
    base = event.start
    while steps < 5000:
        if freq == "DAILY":
            candidates = [base + dt.timedelta(days=steps * interval)]
        elif freq == "WEEKLY":
            week = base + dt.timedelta(weeks=steps * interval)
            monday = week - dt.timedelta(days=week.weekday())
            days = by_day or [base.weekday()]
            candidates = [monday + dt.timedelta(days=d) for d in sorted(days)]
        elif freq == "MONTHLY":
            candidates = [_add_months(base, steps * interval)]
        elif freq == "YEARLY":
            candidates = [_add_months(base, 12 * steps * interval)]
        else:
            if base < until:
                yield base
            return
        steps += 1
        for when in candidates:
            if when is None or when < base:
                continue
            if when >= rule_end:
                return
            produced += 1
            if count is not None and produced > count:
                return
            if when in event.exdates:
                continue
            yield when


def _add_months(moment: dt.datetime, months: int) -> dt.datetime | None:
    month = moment.month - 1 + months
    year, month = moment.year + month // 12, month % 12 + 1
    try:
        return moment.replace(year=year, month=month)
    except ValueError:
        return None  # the 31st in a short month: skipped, like calendars do


# -- sources -------------------------------------------------------------------------------
def evolution_texts(home: Path | None = None) -> list[str]:
    """iCalendar text from Evolution Data Server's local calendars and online-account caches."""
    home = home or Path.home()
    texts = []
    for path in (home / ".local/share/evolution/calendar").glob("*/calendar.ics"):
        try:
            texts.append(path.read_text(errors="replace"))
        except OSError:
            pass
    for path in (home / ".cache/evolution/calendar").glob("*/cache.db"):
        try:
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1) as db:
                rows = db.execute("SELECT ECacheOBJ FROM ECacheObjects").fetchall()
            texts.append("\n".join(f"BEGIN:VCALENDAR\n{r[0]}\nEND:VCALENDAR" for r in rows if r[0]))
        except sqlite3.Error:
            continue
    return texts


def fetch(source: str) -> str:
    source = source.strip()
    if source.startswith(("http://", "https://", "webcal://")):
        import urllib.request

        url = "https://" + source[len("webcal://"):] if source.startswith("webcal://") else source
        request = urllib.request.Request(url, headers={"User-Agent": "CornerWindow"})
        with urllib.request.urlopen(request, timeout=8) as response:
            return response.read(4_000_000).decode("utf-8", "replace")
    return Path(source).expanduser().read_text(errors="replace")


def format_next(occ: Occurrence, now: dt.datetime) -> str:
    """'Standup · 11:30', 'Standup · in 25 min', 'Standup · now', 'Dentist · Tue 9:00'."""
    if occ.all_day:
        day = occ.start.date()
        when = "today" if day == now.date() else "tomorrow" if day == now.date() + dt.timedelta(days=1) \
            else occ.start.strftime("%a")
        return f"{occ.summary} · {when}"
    if occ.start <= now < occ.end:
        return f"{occ.summary} · now"
    minutes = (occ.start - now).total_seconds() / 60
    if 0 <= minutes < 60:
        return f"{occ.summary} · in {max(1, round(minutes))} min"
    local = occ.start.astimezone(now.tzinfo)
    clock = f"{local.hour}:{local.minute:02d}"
    if local.date() == now.date():
        return f"{occ.summary} · {clock}"
    if local.date() == now.date() + dt.timedelta(days=1):
        return f"{occ.summary} · tomorrow {clock}"
    return f"{occ.summary} · {local.strftime('%a')} {clock}"


class CalendarService:
    """Loads events off the main thread; at most every `refresh_minutes`."""

    def __init__(self, bus, section: dict) -> None:
        self.bus = bus
        self.section = section
        self.events: list[Event] = []
        self.loaded_at = 0.0
        self._lock = threading.Lock()
        self._loading = False

    def reconfigure(self, section: dict) -> None:
        self.section = section
        self.loaded_at = 0.0

    def refresh(self, force: bool = False) -> None:
        stale = time.monotonic() - self.loaded_at > float(self.section.get("refresh_minutes", 10)) * 60
        if self._loading or not (force or stale or not self.loaded_at):
            return
        self._loading = True

        def load() -> list[Event]:
            texts = evolution_texts() if self.section.get("evolution", True) else []
            for source in self.section.get("sources", []) or []:
                try:
                    texts.append(fetch(str(source)))
                except Exception as exc:
                    _log.info("Calendar %s: %s", source, exc)
            events = []
            for text in texts:
                events += parse_ics(text)
            return events

        def done(events: list[Event]) -> None:
            self._loading = False
            self.events = events
            self.loaded_at = time.monotonic()
            self.bus.publish("calendar.changed")

        tasks.run_async(load, done, error=lambda _e: setattr(self, "_loading", False))

    def upcoming(self, count: int = 5, days: int = 7, now: dt.datetime | None = None) -> list[Occurrence]:
        now = now or dt.datetime.now().astimezone()
        found = occurrences(self.events, now, now + dt.timedelta(days=days))
        # an all-day event that is already under way is noise next to timed ones
        return [o for o in found if not (o.all_day and o.start < now and o.end > now + dt.timedelta(hours=1)
                                         and len(found) > 1)][:count]

    def days_with_events(self, year: int, month: int) -> set[dt.date]:
        local = dt.datetime.now().astimezone().tzinfo
        first = dt.datetime(year, month, 1, tzinfo=local)
        last = dt.datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=local)
        out = set()
        for occ in occurrences(self.events, first, last, limit=2000):
            day = occ.start.astimezone(local).date()
            while day < occ.end.astimezone(local).date() or day == occ.start.astimezone(local).date():
                if first.date() <= day < last.date():
                    out.add(day)
                day += dt.timedelta(days=1)
                if not occ.all_day or day >= occ.end.astimezone(local).date():
                    break
        return out
