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

    if command == "agent":
        return _agent(argv[2:])

    if command == "taskbar" and not _instance_running():
        # the escape hatch: bring the taskbar back even when CornerWindow isn't running
        from .platform.desktop.taskbar import Taskbar, command

        output, status = command(Taskbar(), argv[2:])
        print(output)
        return status

    from .platform.session import detect, gdk_backend_override

    backend = gdk_backend_override(detect())
    if backend:
        os.environ["GDK_BACKEND"] = backend

    from . import demo

    if demo.enabled():
        demo.isolate()  # a throwaway home, its own D-Bus name: your real setup is never touched

    from .app import CornerWindowApp

    return CornerWindowApp().run(argv)


def _agent(args: list[str]) -> int:
    """`cornerwindow agent working|waiting|done|idle`, called from coding agents' hooks. Hooks run
    often, so this never starts the panel and never loads GTK, and it only fails on bad arguments."""
    import json

    if args[:1] == ["statusline"]:
        return _statusline(args[1:])
    if args[:1] == ["setup"]:
        from .platform.agent_setup import main as setup

        return setup(args[1:])
    from .platform.agents import parse_report

    try:
        report = parse_report(args, os.getcwd())
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    from gi.repository import GLib

    from . import APP_ID, demo
    from .platform import dbus

    app_id = f"{APP_ID}.Demo" if demo.enabled() else APP_ID
    try:
        bus = dbus.session()
        if dbus.name_has_owner(bus, app_id):
            dbus.call(bus, app_id, "/" + app_id.replace(".", "/"), "org.gtk.Actions", "Activate",
                      GLib.Variant("(sava{sv})", ("agent-report", [GLib.Variant("s", json.dumps(report))], {})))
    except Exception:  # not running, or no session bus: nothing to tell
        pass
    return 0


def _statusline(args: list[str]) -> int:
    """`cornerwindow agent statusline [--then CMD]` as Claude Code's status line: saves the real
    5-hour and weekly limit % it is handed for the AI agents card, and prints a short line (or
    CMD's, fed the same input, to keep a status line you already had)."""
    import json
    import subprocess

    from .platform.agents import record_statusline, statusline_text

    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = {}
    try:
        record_statusline(payload)
    except Exception:  # a status line must always print something
        pass
    if args[:1] == ["--then"] and len(args) > 1:
        try:
            done = subprocess.run(" ".join(args[1:]), shell=True, input=raw, text=True,
                                  capture_output=True, timeout=10)
            sys.stdout.write(done.stdout)
            return done.returncode
        except (OSError, subprocess.SubprocessError):
            return 1
    print(statusline_text(payload))
    return 0


def _instance_running() -> bool:
    from . import APP_ID
    from .platform import dbus

    try:
        return dbus.name_has_owner(dbus.session(), APP_ID)
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
