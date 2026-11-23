"""Month grids for the calendar (pure, tested)."""

from __future__ import annotations

import calendar
import datetime as dt

WEEK_STARTS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4,
               "saturday": 5, "sunday": 6}


def first_weekday(name: str) -> int:
    return WEEK_STARTS.get(str(name).lower(), 0)


def month_grid(year: int, month: int, week_start: int = 0) -> list[list[dt.date]]:
    """Always 6 weeks of 7 days, starting on `week_start` (0 = Monday), padded with the
    neighbouring months' days so the grid never changes height."""
    first = dt.date(year, month, 1)
    offset = (first.weekday() - week_start) % 7
    start = first - dt.timedelta(days=offset)
    days = [start + dt.timedelta(days=i) for i in range(42)]
    return [days[i:i + 7] for i in range(0, 42, 7)]


def weekday_letters(week_start: int = 0) -> list[str]:
    names = list(calendar.day_abbr)  # locale aware, Monday first
    return [names[(week_start + i) % 7][:2] for i in range(7)]


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1
