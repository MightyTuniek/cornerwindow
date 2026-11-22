"""Filesystem locations (XDG compliant)."""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent.parent
# When running from a source checkout, configs live in <repo>/config.
# When installed (pipx/wheel), they are shipped as package data in cornerwindow/data.
_REPO_CONFIG = PACKAGE_DIR.parent.parent / "config"
_PKG_CONFIG = PACKAGE_DIR / "data"


def shipped_config_dir() -> Path:
    if (_REPO_CONFIG / "default.toml").exists():
        return _REPO_CONFIG
    return _PKG_CONFIG


def _xdg(var: str, fallback: str) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else Path.home() / fallback


def user_config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "cornerwindow"


def user_config_file() -> Path:
    return user_config_dir() / "config.toml"


def user_modules_dir() -> Path:
    return user_config_dir() / "modules"


def data_dir() -> Path:
    path = _xdg("XDG_DATA_HOME", ".local/share") / "cornerwindow"
    path.mkdir(parents=True, exist_ok=True)
    return path


def runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    path = Path(base) / "cornerwindow"
    path.mkdir(parents=True, exist_ok=True)
    return path
