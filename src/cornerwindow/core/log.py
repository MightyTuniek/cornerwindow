"""Logging setup. `CORNERWINDOW_DEBUG=1` turns on debug output."""

from __future__ import annotations

import logging
import os


def setup(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose or os.environ.get("CORNERWINDOW_DEBUG") else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname).1s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"cw.{name}")
