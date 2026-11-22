"""Running helper programs safely (no shell, short timeouts)."""

from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache

from ..core import log

_log = log.get("proc")


@lru_cache(maxsize=None)
def which(program: str) -> str | None:
    return shutil.which(program)


def run(args: list[str], timeout: float = 3.0, check: bool = False) -> str:
    """Run a command and return stdout ('' on failure unless check=True)."""
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        if check:
            raise
        _log.debug("%s failed: %s", args[0], exc)
        return ""
    if result.returncode != 0:
        if check:
            raise RuntimeError(f"{' '.join(args)}: {result.stderr.strip()}")
        _log.debug("%s exited %s: %s", args, result.returncode, result.stderr.strip())
    return result.stdout


def spawn(args: list[str]) -> None:
    """Fire and forget."""
    try:
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError as exc:
        _log.warning("Could not start %s: %s", args[0], exc)
