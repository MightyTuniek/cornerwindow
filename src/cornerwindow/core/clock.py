"""Times, dates and time zones for the command bar (pure and tested: `now` is always passed in).

    time in tokyo          3pm london to tokyo      now + 90m      tomorrow + 2w
    days until dec 25      dec 25                    week          @1700000000
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from functools import lru_cache

try:
    from zoneinfo import ZoneInfo, available_timezones
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]


@dataclass
class Answer:
    text: str  # what the command bar shows
    copy: str  # what Enter copies


# ── time zones ──────────────────────────────────────────────────────────────────────────
_ALIASES = {
    "utc": "UTC", "gmt": "UTC", "z": "UTC",
    "est": "America/New_York", "edt": "America/New_York", "et": "America/New_York",
    "cst": "America/Chicago", "cdt": "America/Chicago", "ct": "America/Chicago",
    "mst": "America/Denver", "mdt": "America/Denver",
    "pst": "America/Los_Angeles", "pdt": "America/Los_Angeles", "pt": "America/Los_Angeles",
    "cet": "Europe/Paris", "cest": "Europe/Paris", "eet": "Europe/Athens", "bst": "Europe/London",
    "ist": "Asia/Kolkata", "jst": "Asia/Tokyo", "kst": "Asia/Seoul", "aest": "Australia/Sydney",
    "nyc": "America/New_York", "ny": "America/New_York", "la": "America/Los_Angeles",
    "sf": "America/Los_Angeles", "san francisco": "America/Los_Angeles", "seattle": "America/Los_Angeles",
    "boston": "America/New_York", "washington": "America/New_York", "miami": "America/New_York",
    "beijing": "Asia/Shanghai", "delhi": "Asia/Kolkata", "mumbai": "Asia/Kolkata", "bangalore": "Asia/Kolkata",
    "uk": "Europe/London", "england": "Europe/London", "britain": "Europe/London",
    "germany": "Europe/Berlin", "france": "Europe/Paris", "spain": "Europe/Madrid", "italy": "Europe/Rome",
    "poland": "Europe/Warsaw", "netherlands": "Europe/Amsterdam", "sweden": "Europe/Stockholm",
    "norway": "Europe/Oslo", "finland": "Europe/Helsinki", "ukraine": "Europe/Kyiv", "kiev": "Europe/Kyiv",
    "russia": "Europe/Moscow", "turkey": "Europe/Istanbul", "greece": "Europe/Athens",
    "japan": "Asia/Tokyo", "china": "Asia/Shanghai", "india": "Asia/Kolkata", "korea": "Asia/Seoul",
    "taiwan": "Asia/Taipei", "thailand": "Asia/Bangkok", "vietnam": "Asia/Ho_Chi_Minh",
    "australia": "Australia/Sydney", "new zealand": "Pacific/Auckland", "brazil": "America/Sao_Paulo",
    "mexico": "America/Mexico_City", "argentina": "America/Argentina/Buenos_Aires",
    "canada": "America/Toronto", "usa": "America/New_York", "us": "America/New_York",
    "israel": "Asia/Jerusalem", "egypt": "Africa/Cairo", "south africa": "Africa/Johannesburg",
    "dubai": "Asia/Dubai", "uae": "Asia/Dubai", "hawaii": "Pacific/Honolulu", "iceland": "Atlantic/Reykjavik",
}


@lru_cache(maxsize=1)
def _zone_index() -> dict[str, str]:
    index: dict[str, str] = {}
    try:
        keys = available_timezones()
    except Exception:
        keys = set()
    for key in sorted(keys):
        if key.startswith(("posix/", "right/", "Etc/", "SystemV/")):
            continue
        index.setdefault(key.lower(), key)
        if "/" in key:
            city = key.rsplit("/", 1)[1].replace("_", " ").lower()
            index.setdefault(city, key)
    return index


def find_zone(name: str):
    """'tokyo' -> ('Tokyo', ZoneInfo('Asia/Tokyo')); None when it isn't a place we know."""
    if ZoneInfo is None:
        return None
    wanted = " ".join(name.strip().lower().replace("_", " ").split())
    if not wanted or wanted.isdigit():
        return None
    key = _ALIASES.get(wanted) or _zone_index().get(wanted)
    if key is None:
        return None
    try:
        zone = ZoneInfo(key)
    except Exception:
        return None
    label = wanted.upper() if len(wanted) <= 4 else wanted.title()
    return label, zone


# ── parsing ─────────────────────────────────────────────────────────────────────────────
_TOD = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm|a|p)?$")
_TOD4 = re.compile(r"^(\d{2})(\d{2})$")


def parse_time_of_day(text: str) -> tuple[int, int] | None:
    """'7:30' / '7pm' / '7:30 pm' / '19:30' / '1930' / 'noon' -> (hour, minute)."""
    text = text.strip().lower().replace(".", ":")
    if text in ("noon", "midday"):
        return 12, 0
    if text == "midnight":
        return 0, 0
    match = _TOD.match(text)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2) or 0)
        meridiem = match.group(3)
        if meridiem:
            if not 1 <= hour <= 12:
                return None
            hour = hour % 12 + (12 if meridiem.startswith("p") else 0)
        if hour > 23 or minute > 59:
            return None
        return hour, minute
    match = _TOD4.match(text)
    if match and int(match.group(1)) <= 23 and int(match.group(2)) <= 59:
        return int(match.group(1)), int(match.group(2))
    return None


def seconds_until(hour: int, minute: int, now: dt.datetime) -> float:
    """Seconds until the next hour:minute (tomorrow if it already passed today)."""
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += dt.timedelta(days=1)
    return (target - now).total_seconds()


_DURATION_UNITS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
    "w": 604800, "wk": 604800, "wks": 604800, "week": 604800, "weeks": 604800,
    "y": 31536000, "yr": 31536000, "year": 31536000, "years": 31536000,
}
_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)\s*([a-z]+)")


def parse_duration(text: str) -> dt.timedelta | None:
    """'90m' / '1h30m' / '3 days' / '2 weeks 1 day' -> timedelta."""
    text = text.strip().lower().replace(",", " ").replace(" and ", " ")
    if not text:
        return None
    total, end = 0.0, 0
    for match in _DURATION_PART.finditer(text):
        if text[end:match.start()].strip():
            return None
        unit = _DURATION_UNITS.get(match.group(2))
        if unit is None:
            return None
        total += float(match.group(1)) * unit
        end = match.end()
    if end == 0 or text[end:].strip():
        return None
    return dt.timedelta(seconds=total)


_MONTHS = {name: i for i, names in enumerate(
    [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",),
     ("jun", "june"), ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"),
     ("oct", "october"), ("nov", "november"), ("dec", "december")], start=1) for name in names}
_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_DOTTED = re.compile(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})$")
_NAMED = re.compile(r"^(?:([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?|(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+))(?:,?\s+(\d{4}))?$")


def parse_date(text: str, today: dt.date) -> dt.date | None:
    """'2026-12-25', '25.12.2026', 'dec 25', '25 december 2027', 'tomorrow', 'christmas'."""
    text = " ".join(text.strip().lower().split())
    words = {"today": 0, "tomorrow": 1, "yesterday": -1}
    if text in words:
        return today + dt.timedelta(days=words[text])
    if text in ("christmas", "xmas"):
        text = "dec 25"
    if text in ("new year", "new years", "new year's", "newyear"):
        return dt.date(today.year + 1, 1, 1)
    try:
        match = _ISO.match(text)
        if match:
            return dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        match = _DOTTED.match(text)
        if match:
            return dt.date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        match = _NAMED.match(text)
        if match:
            month_name = match.group(1) or match.group(4)
            day = int(match.group(2) or match.group(3))
            month = _MONTHS.get(month_name)
            if month is None:
                return None
            if match.group(5):
                return dt.date(int(match.group(5)), month, day)
            date = dt.date(today.year, month, day)
            return date if date >= today else dt.date(today.year + 1, month, day)
    except ValueError:
        return None
    return None


# ── formatting (English names on purpose: the surrounding text is English, and the C
#    library's date names follow whatever locale GTK set) ────────────────────────────────
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]


def _hm(moment: dt.datetime) -> str:
    return f"{moment.hour:02d}:{moment.minute:02d}"


def _wd(date: dt.date, short: bool = True) -> str:
    name = WEEKDAYS[date.weekday()]
    return name[:3] if short else name


def _day(date: dt.date) -> str:
    return f"{_wd(date)} {date.day} {MONTHS[date.month - 1][:3]} {date.year}"


def _long(date: dt.date) -> str:
    return f"{_wd(date, False)} {date.day} {MONTHS[date.month - 1]} {date.year}"


def _offset(moment: dt.datetime) -> str:
    offset = moment.utcoffset() or dt.timedelta()
    minutes = int(offset.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "−"
    hours, mins = divmod(abs(minutes), 60)
    return f"UTC{sign}{hours}" + (f":{mins:02d}" if mins else "")


def _days_phrase(days: int) -> str:
    if days == 0:
        return "today"
    count = abs(days)
    span = f"{count} day{'s' if count != 1 else ''}"
    if count >= 14:
        weeks, rest = divmod(count, 7)
        span += f" ({weeks} weeks" + (f" {rest} day{'s' if rest != 1 else ''})" if rest else ")")
    return f"in {span}" if days > 0 else f"{span} ago"


def _shift_label(target: dt.date, reference: dt.date) -> str:
    diff = (target - reference).days
    return "" if diff == 0 else (" (+1 day)" if diff == 1 else " (−1 day)" if diff == -1 else f" ({diff:+d} days)")


# ── the evaluator ───────────────────────────────────────────────────────────────────────
_ZONE_NOW = re.compile(r"^(?:(?:what\s+)?time|now|clock)\s+(?:is\s+it\s+)?(?:in\s+)?(.+)$")
_ZONE_NOW_SUFFIX = re.compile(r"^(.+?)\s+(?:time|now)$")
_CONVERT_TIME = re.compile(r"^(.+?)\s+(?:in|to|->)\s+(.+)$")
_SHIFT_WORD = re.compile(r"^(now|today|tomorrow|yesterday)\s*([+-])\s*(.+)$")
_SHIFT = re.compile(r"^(.+?)\s+([+-])\s+(.+)$")
_IN = re.compile(r"^in\s+(.+)$")
_UNTIL = re.compile(r"^(?:(?:days?|time|how\s+long)\s+)?(until|till|til|to|since|from)\s+(.+)$")
_EPOCH = re.compile(r"^@(\d{9,11})$")


def evaluate(text: str, now: dt.datetime) -> Answer | None:
    """An answer for a time/date question, or None. `now` must be timezone-aware (local)."""
    q = " ".join(text.strip().lower().split())
    if not q:
        return None
    today = now.date()
    if q in ("now", "time", "clock"):
        return Answer(f"{_hm(now)} · {_day(today)}", _hm(now))
    if q in ("today", "date"):
        week = today.isocalendar()[1]
        return Answer(f"{_long(today)} · week {week} · day {today.timetuple().tm_yday}", today.isoformat())
    if q in ("week", "week number", "weeknumber", "kw"):
        week = today.isocalendar()[1]
        return Answer(f"Week {week}", str(week))
    if q in ("unix", "epoch", "timestamp", "unix time"):
        stamp = str(int(now.timestamp()))
        return Answer(stamp, stamp)
    match = _EPOCH.match(q)
    if match:
        moment = dt.datetime.fromtimestamp(int(match.group(1)), tz=now.tzinfo)
        return Answer(f"{_hm(moment)} · {_day(moment.date())}", moment.isoformat(timespec="seconds"))

    # time in a zone: "time in tokyo", "tokyo time"
    match = _ZONE_NOW.match(q) or _ZONE_NOW_SUFFIX.match(q)
    if match:
        found = find_zone(match.group(1))
        if found:
            name, zone = found
            there = now.astimezone(zone)
            return Answer(f"{_hm(there)} {_wd(there.date())} in {name} · {_offset(there)}"
                          f"{_shift_label(there.date(), today)}", _hm(there))

    # convert a time between zones: "3pm in tokyo", "15:00 london to new york"
    match = _CONVERT_TIME.match(q)
    if match:
        target = find_zone(match.group(2))
        if target:
            source = _time_with_zone(match.group(1), now)
            if source is not None:
                moment, source_name = source
                there = moment.astimezone(target[1])
                origin = f"{_hm(moment)} {source_name}" if source_name else _hm(moment)
                return Answer(f"{_hm(there)} in {target[0]}{_shift_label(there.date(), moment.date())}"
                              f"  ← {origin}", _hm(there))

    # date maths: "now + 90m", "tomorrow + 2w", "dec 25 - 10 days", "in 3 days"
    match = _IN.match(q)
    if match:
        delta = parse_duration(match.group(1))
        if delta is not None:
            return _shifted(now, delta, precise=delta.total_seconds() % 86400 != 0)
    match = _SHIFT_WORD.match(q) or _SHIFT.match(q)
    if match:
        delta = parse_duration(match.group(3))
        if delta is not None:
            if match.group(2) == "-":
                delta = -delta
            base = match.group(1).strip()
            if base == "now":
                return _shifted(now, delta, precise=True)
            date = parse_date(base, today)
            if date is not None:
                if delta.total_seconds() % 86400:
                    moment = dt.datetime.combine(date, dt.time(), now.tzinfo) + delta
                    return _shifted(moment, dt.timedelta(), precise=True, today=today)
                return _date_answer(date + delta, today)

    # countdowns: "days until dec 25", "until christmas", "since 2020-01-01"
    match = _UNTIL.match(q)
    if match:
        date = parse_date(match.group(2), today)
        if date is not None and match.group(1) in ("since", "from") and date > today and not _has_year(match.group(2)):
            date = date.replace(year=date.year - 1)
        if date is not None:
            return _date_answer(date, today)

    # a bare date: "dec 25", "2027-01-01"
    if q not in ("today", "tomorrow", "yesterday"):
        date = parse_date(q, today)
        if date is not None and (q[0].isdigit() or q.split()[0] in _MONTHS or q in ("christmas", "xmas", "new year")):
            return _date_answer(date, today)
    elif q in ("tomorrow", "yesterday"):
        return _date_answer(parse_date(q, today), today)
    return None


def _has_year(text: str) -> bool:
    return bool(re.search(r"\d{4}", text))


def _time_with_zone(text: str, now: dt.datetime):
    """'3pm' -> (today 15:00 local, ''); '3 pm london' -> (today 15:00 London, 'London')."""
    tokens = text.split()
    for split in (2, 1):
        if len(tokens) < split:
            continue
        tod = parse_time_of_day(" ".join(tokens[:split]))
        if tod is None:
            continue
        rest = " ".join(tokens[split:])
        if not rest:
            zone, name = now.tzinfo, ""
        else:
            found = find_zone(rest)
            if found is None:
                continue
            name, zone = found
        base = now.astimezone(zone)
        return base.replace(hour=tod[0], minute=tod[1], second=0, microsecond=0), name
    if text in ("now", "time"):
        return now, ""
    return None


def _shifted(moment_or_now: dt.datetime, delta: dt.timedelta, precise: bool,
             today: dt.date | None = None) -> Answer:
    moment = moment_or_now + delta
    reference = today or moment_or_now.date()
    if precise:
        days = (moment.date() - reference).days
        when = "" if days == 0 else f" · {_day(moment.date())}"
        return Answer(f"{_hm(moment)}{when}", f"{moment.date().isoformat()} {_hm(moment)}")
    return _date_answer(moment.date(), reference)


def _date_answer(date: dt.date, today: dt.date) -> Answer:
    days = (date - today).days
    return Answer(f"{_long(date)} · {_days_phrase(days)}", date.isoformat())
