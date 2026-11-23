"""Processes and ports from the command bar: `top` (the busiest, Enter twice stops one),
`kill firefox`, `port 3000` (what listens there; Enter twice stops it), `ports`, `ip`.
A tool: no card (the System card's full view lists the busiest processes too)."""

from __future__ import annotations

import socket

from ..platform import processes
from .base import Command, Module, Suggestion, module


@module("processes", title="Processes", area="tool", icon="process",
        description="Busiest processes, stop one by name, what listens on a port, your IP")
class Processes(Module):
    def setup(self) -> None:
        self.sampler = processes.ProcessSampler()
        self.latest: list[processes.Proc] = []

    def build(self):
        self.ctx.every(2.0, self._sample)  # only while the panel is open
        return None

    def _sample(self) -> None:
        self.ctx.run_async(lambda: self.sampler.top(12), lambda found: setattr(self, "latest", found))

    def _stop_row(self, proc: processes.Proc, detail: str) -> Suggestion:
        return Suggestion(f"Stop {proc.name}", f"{detail} · pid {proc.pid} · Enter twice",
                          lambda p=proc: processes.stop(p.pid), "process", confirm=True)

    def commands(self) -> list[Command]:
        def top_rows(_arg: str) -> list[Suggestion]:
            if not self.latest:
                return [Suggestion("Measuring…", "Type again in a moment", lambda: None, "process")]
            return [self._stop_row(p, f"{p.cpu:.0f}% CPU · {p.rss_mb:.0f} MB") for p in self.latest[:7] if p.user] or \
                [Suggestion("Nothing of yours is busy", "", lambda: None, "process")]

        def kill_rows(arg: str) -> list[Suggestion]:
            arg = arg.strip()
            force = arg.startswith("-9 ")
            name = arg[3:] if force else arg
            if not name:
                return [Suggestion("Type a program's name: kill firefox", "kill -9 firefox forces it", lambda: None,
                                   "process")]
            found = [p for p in processes.find(name, processes.all_processes()) if p.user]
            if not found:
                return [Suggestion(f"No process of yours matches {name!r}", "", lambda: None, "process")]
            if len(found) > 1:
                rows = [Suggestion(f"Stop all {len(found)} {found[0].name if len({p.name for p in found}) == 1 else 'matches'}",
                                   "Enter twice", lambda ps=found, f=force: _stop_all(ps, f), "process", confirm=True)]
            else:
                rows = []
            for p in found[:6]:
                rows.append(Suggestion(f"{'Force-stop' if force else 'Stop'} {p.name}", f"pid {p.pid} · Enter twice",
                                       lambda p=p, f=force: processes.stop(p.pid, f), "process", confirm=True))
            return rows

        def port_rows(arg: str) -> list[Suggestion]:
            arg = arg.strip()
            listening = processes.listeners()
            if arg:
                if not arg.isdigit():
                    return [Suggestion("Type a port number: port 3000", "", lambda: None, "process")]
                listening = [(s, o) for s, o in listening if s.port == int(arg)]
                if not listening:
                    return [Suggestion(f"Nothing listens on port {arg}", "", lambda: None, "process")]
            rows = []
            for sock, owner in listening[:7]:
                where = f"{sock.proto} {sock.address}:{sock.port}"
                if owner is not None and owner.user:
                    rows.append(Suggestion(f"{sock.port} · {owner.name}", f"{where} · Enter twice stops it",
                                           lambda o=owner: processes.stop(o.pid), "process", confirm=True))
                else:
                    rows.append(Suggestion(f"{sock.port} · {owner.name if owner else 'system'}", where,
                                           lambda: None, "process"))
            return rows or [Suggestion("Nothing is listening", "", lambda: None, "process")]

        def ip_rows(_arg: str) -> list[Suggestion]:
            address = local_ip()
            if not address:
                return [Suggestion("Not connected", "", lambda: None, "web")]
            return [Suggestion(f"= {address}", f"{socket.gethostname()} · Enter copies",
                               lambda a=address: _copy(a), "web")]

        return [
            Command(("top",), "Busiest processes", "top", lambda _a: None, True, suggest=top_rows),
            Command(("kill",), "Stop a program", "kill firefox · kill -9 firefox", lambda _a: None, True,
                    suggest=kill_rows),
            Command(("port", "ports"), "What listens on a port", "port 3000 · ports", lambda _a: None, True,
                    suggest=port_rows),
            Command(("ip",), "Your local IP address", "ip", lambda _a: _copy(local_ip() or ""), suggest=ip_rows),
        ]


def _stop_all(procs, force: bool) -> str:
    for p in procs:
        processes.stop(p.pid, force)
    return f"Stopped {len(procs)}"


def local_ip() -> str:
    """The address other machines on your network reach you at (no packet is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # TEST-NET: routing only
            return s.getsockname()[0]
    except OSError:
        return ""


def _copy(text: str) -> str:
    from gi.repository import Gdk, Gtk

    Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)
    return f"Copied {text}"
