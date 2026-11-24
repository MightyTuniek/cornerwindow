"""Shared test setup."""

import locale

import pytest


@pytest.fixture(autouse=True)
def _english_dates():
    """Importing GTK switches the process to the desktop's locale, which turns "Monday" into the
    user's language for every test that runs after it. Date formatting is tested in English."""
    before = locale.setlocale(locale.LC_TIME)
    locale.setlocale(locale.LC_TIME, "C")
    yield
    locale.setlocale(locale.LC_TIME, before)
