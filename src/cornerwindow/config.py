"""Layered TOML configuration.

Layers, later wins:
  1. <config>/default.toml                       every option, documented
  2. <config>/machines/*.toml whose [match] fits  per-PC tuning (hostname / monitor model)
  3. ~/.config/cornerwindow/config.toml           the user's own overrides

Tables are merged recursively; arrays and scalars are replaced wholesale.
"""

from __future__ import annotations

import os
import socket
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .core import log, paths

_log = log.get("config")


def deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _read(path: Path) -> dict:
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as exc:
        _log.error("Ignoring %s: %s", path, exc)
        return {}


def machine_matches(match: dict, facts: dict) -> bool:
    """A machine file applies when any of its [match] keys matches a fact.

    match = { hostname = ["my-desktop"], monitor_model = ["VG240Y"] }
    facts = { "hostname": "my-desktop", "monitor_models": ["Acer VG240Y"] }
    """
    if not match:
        return False
    hostnames = _as_list(match.get("hostname"))
    if facts.get("hostname") in hostnames:
        return True
    models = [m.lower() for m in facts.get("monitor_models", [])]
    for wanted in _as_list(match.get("monitor_model")):
        if any(wanted.lower() in m for m in models):
            return True
    return False


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


@dataclass
class Config:
    data: dict  # everything merged
    sources: list[Path] = field(default_factory=list)
    base: dict = field(default_factory=dict)  # default + machine layers (what "reset" returns to)
    user: dict = field(default_factory=dict)  # the user's own file, raw
    user_file: Path | None = None
    facts: dict = field(default_factory=dict)

    def origin(self, section: str, key: str) -> str:
        """Which layer a value comes from: 'yours', 'machine', 'default' or '' (unset)."""
        if key in self.user.get(section, {}):
            return "yours"
        if key in self.base.get(section, {}):
            return "machine" if self._machine_value(section, key) else "default"
        return ""

    def _machine_value(self, section: str, key: str) -> bool:
        return any(key in layer.get(section, {}) for layer in self._machine_layers)

    _machine_layers: list = field(default_factory=list, repr=False)

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def section(self, name: str) -> dict:
        value = self.data.get(name, {})
        return value if isinstance(value, dict) else {}


def gather_facts(monitor_models: Iterable[str] = ()) -> dict:
    return {"hostname": socket.gethostname(), "monitor_models": list(monitor_models)}


def load(facts: dict | None = None, config_dir: Path | None = None,
         user_file: Path | None = None) -> Config:
    config_dir = config_dir or paths.shipped_config_dir()
    user_file = user_file or paths.user_config_file()
    facts = facts or gather_facts()

    sources: list[Path] = []
    default_path = config_dir / "default.toml"
    data = _read(default_path)
    sources.append(default_path)

    forced = os.environ.get("CORNERWINDOW_MACHINE")
    machine_layers = []
    for machine in sorted((config_dir / "machines").glob("*.toml")):
        raw = _read(machine)
        match = raw.pop("match", {})
        if (forced and machine.stem == forced) or (not forced and machine_matches(match, facts)):
            data = deep_merge(data, raw)
            machine_layers.append(raw)
            sources.append(machine)

    base = data
    user = _read(user_file) if user_file.exists() else {}
    if user_file.exists():
        data = deep_merge(data, user)
        sources.append(user_file)

    _log.debug("Config layers: %s", ", ".join(str(s) for s in sources))
    return Config(data, sources, base=base, user=user, user_file=user_file, facts=facts,
                  _machine_layers=machine_layers)


USER_TEMPLATE = """\
# CornerWindow user overrides. Anything here wins over the shipped defaults
# and your machine file. See {default} for every option.
#
# [general]
# corner = "top-right"
#
# [trigger]
# radius = 20
"""


def ensure_user_file() -> Path:
    path = paths.user_config_file()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(USER_TEMPLATE.format(default=paths.shipped_config_dir() / "default.toml"))
    return path
