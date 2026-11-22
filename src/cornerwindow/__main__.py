"""Command line entry point: `cornerwindow [run|toggle|open|close|set|get|doctor|...]`."""

from __future__ import annotations

import os
import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    from .core import log

    log.setup(verbose="-v" in argv or "--verbose" in argv)
    argv = [a for a in argv if a not in ("-v", "--verbose")]
    command = argv[1] if len(argv) > 1 else "run"

    if command == "doctor":
        from .doctor import run as doctor

        return doctor()
    if command in ("version", "--version"):
        from . import __version__

        print(__version__)
        return 0

    from .platform.session import detect, gdk_backend_override

    backend = gdk_backend_override(detect())
    if backend:
        os.environ["GDK_BACKEND"] = backend

    from .app import CornerWindowApp

    return CornerWindowApp().run(argv)


if __name__ == "__main__":
    sys.exit(main())
