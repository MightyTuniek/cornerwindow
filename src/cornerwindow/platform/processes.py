"""Processes and network ports from /proc: what's busy, what listens on a port, stopping them.

The parsers are pure (text in, data out) so the tests can feed them sample /proc files.
"""

from __future__ import annotations

import os
import signal
from dataclasses import dataclass
from pathlib import Path

PROC = Path("/proc")
_TCP_LISTEN = "0A"


@dataclass
class Proc:
    pid: int
    name: str
    cpu: float = 0.0  # % of one core (100 = one core fully busy)
    rss_mb: float = 0.0
    user: bool = True  # owned by us (we may stop it)


def parse_stat(text: str) -> tuple[str, int]:
    """/proc/PID/stat -> (command name, utime + stime in clock ticks). The name is in parentheses
    and may itself contain spaces or parentheses, so split at the last ')'."""
    start, end = text.index("("), text.rindex(")")
    name = text[start + 1:end]
    fields = text[end + 2:].split()
    # fields[0] is the state (field 3); utime and stime are fields 14 and 15
    return name, int(fields[11]) + int(fields[12])


def parse_statm_rss(text: str, page_size: int = 4096) -> float:
    """/proc/PID/statm -> resident memory in MB."""
    parts = text.split()
    return int(parts[1]) * page_size / 1_048_576 if len(parts) > 1 else 0.0


class ProcessSampler:
    """CPU use per process between two calls of `top()`."""

    def __init__(self) -> None:
        self._last: dict[int, int] = {}
        self._last_time = 0.0
        self._ticks = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
        self._uid = os.getuid()

    def top(self, count: int = 5, now: float | None = None) -> list[Proc]:
        import time

        now = time.monotonic() if now is None else now
        elapsed = now - self._last_time if self._last_time else 0.0
        current: dict[int, int] = {}
        found: list[Proc] = []
        for entry in PROC.iterdir() if PROC.is_dir() else []:
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            try:
                name, ticks = parse_stat((entry / "stat").read_text())
                rss = parse_statm_rss((entry / "statm").read_text())
                mine = entry.stat().st_uid == self._uid
            except (OSError, ValueError, IndexError):
                continue
            current[pid] = ticks
            cpu = 0.0
            if elapsed > 0 and pid in self._last:
                cpu = 100.0 * (ticks - self._last[pid]) / self._ticks / elapsed
            found.append(Proc(pid, name, max(0.0, cpu), rss, mine))
        self._last, self._last_time = current, now
        return rank(found, count)


def rank(procs: list[Proc], count: int) -> list[Proc]:
    """Busiest first; memory breaks ties (so the first sample, with no CPU yet, still says something)."""
    return sorted(procs, key=lambda p: (-round(p.cpu, 1), -p.rss_mb))[:count]


def find(name: str, procs: list[Proc]) -> list[Proc]:
    name = name.lower().strip()
    exact = [p for p in procs if p.name.lower() == name]
    return exact or [p for p in procs if name in p.name.lower()]


def all_processes() -> list[Proc]:
    out = []
    uid = os.getuid()
    for entry in PROC.iterdir() if PROC.is_dir() else []:
        if not entry.name.isdigit():
            continue
        try:
            name, _ticks = parse_stat((entry / "stat").read_text())
            out.append(Proc(int(entry.name), name, user=entry.stat().st_uid == uid))
        except (OSError, ValueError, IndexError):
            continue
    return out


def stop(pid: int, force: bool = False) -> str:
    """SIGTERM (or SIGKILL with force). Returns feedback text."""
    try:
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
        return f"Stopped {pid}"
    except ProcessLookupError:
        return f"{pid} has already exited"
    except PermissionError:
        return f"{pid} belongs to another user"


# -- ports ---------------------------------------------------------------------------------
@dataclass
class Listener:
    port: int
    proto: str  # "tcp" | "udp"
    inode: int
    address: str


def parse_net(text: str, proto: str) -> list[Listener]:
    """/proc/net/tcp{,6} or udp{,6} -> listening sockets (TCP in LISTEN, UDP bound)."""
    out = []
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 10:
            continue
        local, state, inode = parts[1], parts[3], parts[9]
        if proto == "tcp" and state != _TCP_LISTEN:
            continue
        address, _, port_hex = local.rpartition(":")
        try:
            port, node = int(port_hex, 16), int(inode)
        except ValueError:
            continue
        if port and node:
            out.append(Listener(port, proto, node, _address(address)))
    return out


def _address(hex_addr: str) -> str:
    if len(hex_addr) == 8:  # IPv4, little-endian
        octets = [str(int(hex_addr[i:i + 2], 16)) for i in range(6, -1, -2)]
        return ".".join(octets)
    return "::" if set(hex_addr) == {"0"} else "IPv6"


def socket_inodes(pid_dir: Path) -> set[int]:
    out = set()
    try:
        for fd in (pid_dir / "fd").iterdir():
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            if target.startswith("socket:["):
                out.add(int(target[8:-1]))
    except OSError:
        pass
    return out


def listeners() -> list[tuple[Listener, Proc | None]]:
    """Every listening port, with the process holding it (None: another user's, so not visible)."""
    sockets: list[Listener] = []
    for name, proto in (("tcp", "tcp"), ("tcp6", "tcp"), ("udp", "udp"), ("udp6", "udp")):
        try:
            sockets += parse_net((PROC / "net" / name).read_text(), proto)
        except OSError:
            continue
    owners: dict[int, Proc] = {}
    wanted = {s.inode for s in sockets}
    uid = os.getuid()
    for entry in PROC.iterdir() if PROC.is_dir() else []:
        if not entry.name.isdigit() or not wanted - set(owners):
            continue
        inodes = socket_inodes(entry) & wanted
        if not inodes:
            continue
        try:
            name, _ticks = parse_stat((entry / "stat").read_text())
            owner = Proc(int(entry.name), name, user=entry.stat().st_uid == uid)
        except (OSError, ValueError, IndexError):
            continue
        for inode in inodes:
            owners[inode] = owner
    seen: set[tuple[int, str]] = set()
    out = []
    for s in sorted(sockets, key=lambda s: (s.port, s.proto)):
        if (s.port, s.proto) in seen:
            continue
        seen.add((s.port, s.proto))
        out.append((s, owners.get(s.inode)))
    return out
