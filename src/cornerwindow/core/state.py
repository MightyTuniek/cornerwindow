"""Small persistent key/value state (JSON), e.g. saved presets or night-light schedule."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from . import log, paths

_log = log.get("state")


class State:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.data_dir() / "state.json"
        self._lock = threading.Lock()
        try:
            self._data: dict[str, Any] = json.loads(self.path.read_text())
        except FileNotFoundError:
            self._data = {}
        except (OSError, ValueError) as exc:
            _log.warning("Could not read %s: %s", self.path, exc)
            self._data = {}

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2))
            tmp.replace(self.path)

    def delete(self, key: str) -> None:
        with self._lock:
            if self._data.pop(key, None) is not None:
                self.path.write_text(json.dumps(self._data, indent=2))
