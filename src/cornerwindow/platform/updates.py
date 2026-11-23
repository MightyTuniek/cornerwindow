"""Available software updates, counted the way each distro's own tools do it.

  apt     /usr/lib/update-notifier/apt-check (Ubuntu, Zorin, Mint), else `apt list --upgradable`
  dnf     `dnf check-update -q`          pacman  `checkupdates` (pacman-contrib)
  flatpak `flatpak remote-ls --updates`  snap    `snap refresh --list`

Parsers are pure, for the tests. Checks run on a worker thread, at most every few hours.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import proc

APT_CHECK = Path("/usr/lib/update-notifier/apt-check")


@dataclass
class Updates:
    system: int = 0
    security: int = 0
    flatpak: int = 0
    snap: int = 0
    names: list[str] = field(default_factory=list)
    source: str = ""  # which tool counted the system updates

    @property
    def total(self) -> int:
        return self.system + self.flatpak + self.snap

    def summary(self) -> str:
        parts = []
        if self.system:
            parts.append(f"{self.system} system" + (f" ({self.security} security)" if self.security else ""))
        if self.flatpak:
            parts.append(f"{self.flatpak} Flatpak")
        if self.snap:
            parts.append(f"{self.snap} Snap")
        return " · ".join(parts) or "Up to date"


def parse_apt_check(text: str) -> tuple[int, int]:
    """apt-check prints "UPDATES;SECURITY" on stderr."""
    try:
        total, security = text.strip().split(";")[:2]
        return int(total), int(security)
    except ValueError:
        return 0, 0


def parse_apt_list(text: str) -> list[str]:
    return [line.split("/", 1)[0] for line in text.splitlines() if "/" in line and "[upgradable" in line]


def parse_dnf(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and "." in parts[0] and not line.startswith(("Last metadata", "Obsoleting", " ")):
            out.append(parts[0].rsplit(".", 1)[0])
    return out


def parse_lines(text: str) -> list[str]:
    """checkupdates ("name old -> new"), flatpak --columns=application: one item per line."""
    return [line.split()[0] for line in text.splitlines() if line.strip() and not line.startswith(("Name", "All snaps"))]


def _run(args: list[str], timeout: float = 60) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def check(flatpak: bool = True, snap: bool = True) -> Updates:
    out = Updates()
    if APT_CHECK.exists():
        done = _run([str(APT_CHECK)])
        if done is not None:
            out.system, out.security = parse_apt_check(done.stderr)
            out.source = "apt"
    elif proc.which("apt"):
        done = _run(["apt", "list", "--upgradable"])
        if done is not None:
            out.names = parse_apt_list(done.stdout)
            out.system, out.source = len(out.names), "apt"
    elif proc.which("dnf"):
        done = _run(["dnf", "check-update", "-q"], 120)
        if done is not None and done.returncode == 100:
            out.names = parse_dnf(done.stdout)
            out.system, out.source = len(out.names), "dnf"
    elif proc.which("checkupdates"):
        done = _run(["checkupdates"], 120)
        if done is not None:
            out.names = parse_lines(done.stdout)
            out.system, out.source = len(out.names), "pacman"
    if flatpak and proc.which("flatpak"):
        done = _run(["flatpak", "remote-ls", "--updates", "--columns=application"], 90)
        if done is not None and done.returncode == 0:
            out.flatpak = len(parse_lines(done.stdout))
    if snap and proc.which("snap"):
        done = _run(["snap", "refresh", "--list"], 60)
        if done is not None and done.returncode == 0:
            out.snap = len(parse_lines(done.stdout))
    return out


def updater() -> list[str] | None:
    """The desktop's own updater, opened on its updates page."""
    for command in (["gnome-software", "--mode=updates"], ["plasma-discover", "--mode", "update"],
                    ["mintupdate"], ["update-manager"], ["pamac-manager", "--updates"], ["gnome-software"]):
        if proc.which(command[0]):
            return command
    return None
