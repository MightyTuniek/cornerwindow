"""System metrics straight from /proc and /sys (no psutil needed)."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

HWMON = Path("/sys/class/hwmon")


@dataclass
class Sensor:
    chip: str
    label: str
    path: Path

    def read(self) -> float | None:
        try:
            return int(self.path.read_text()) / 1000.0
        except (OSError, ValueError):
            return None


def temperature_sensors() -> list[Sensor]:
    sensors = []
    for chip_dir in sorted(HWMON.glob("hwmon*")):
        try:
            chip = (chip_dir / "name").read_text().strip()
        except OSError:
            continue
        for temp in sorted(chip_dir.glob("temp*_input")):
            label_file = temp.with_name(temp.name.replace("_input", "_label"))
            try:
                label = label_file.read_text().strip()
            except OSError:
                label = temp.name.split("_")[0]
            sensors.append(Sensor(chip, label, temp))
    return sensors


CPU_CHIPS = ("k10temp", "coretemp", "zenpower", "cpu_thermal", "acpitz")
CPU_LABELS = ("Tctl", "Tdie", "Package id 0", "temp1")
GPU_CHIPS = ("amdgpu", "nouveau", "radeon", "i915", "xe")


def find_sensor(spec: str, kind: str) -> Sensor | None:
    """spec = 'auto' or 'chip/label' (e.g. 'k10temp/Tctl')."""
    sensors = temperature_sensors()
    if spec and spec != "auto":
        chip, _, label = spec.partition("/")
        for s in sensors:
            if s.chip == chip and (not label or s.label == label):
                return s
        return None
    chips = CPU_CHIPS if kind == "cpu" else GPU_CHIPS
    for chip in chips:
        candidates = [s for s in sensors if s.chip == chip]
        if not candidates:
            continue
        if kind == "cpu":
            for label in CPU_LABELS:
                for s in candidates:
                    if s.label == label:
                        return s
        for s in candidates:
            if s.label in ("edge", "temp1"):
                return s
        return candidates[0]
    return None


class CpuUsage:
    def __init__(self) -> None:
        self._last = self._read()

    @staticmethod
    def _read() -> tuple[int, int]:
        with open("/proc/stat") as fh:
            fields = [int(x) for x in fh.readline().split()[1:]]
        idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
        return idle, sum(fields)

    def sample(self) -> float:
        idle, total = self._read()
        d_idle, d_total = idle - self._last[0], total - self._last[1]
        self._last = (idle, total)
        return 0.0 if d_total <= 0 else 100.0 * (1.0 - d_idle / d_total)


def memory() -> tuple[float, float]:
    """(used_gib, total_gib)"""
    info = {}
    with open("/proc/meminfo") as fh:
        for line in fh:
            key, value = line.split(":", 1)
            info[key] = int(value.split()[0])
    total = info.get("MemTotal", 0)
    available = info.get("MemAvailable", info.get("MemFree", 0))
    return (total - available) / 1048576.0, total / 1048576.0


def _physical_interfaces() -> list[str]:
    return [p.name for p in Path("/sys/class/net").iterdir() if (p / "device").exists()]


class NetSpeed:
    def __init__(self) -> None:
        self._ifaces = _physical_interfaces()
        self._last = (time.monotonic(), *self._read())

    def _read(self) -> tuple[int, int]:
        rx = tx = 0
        with open("/proc/net/dev") as fh:
            for line in fh.readlines()[2:]:
                name, data = line.split(":", 1)
                if name.strip() in self._ifaces:
                    cols = data.split()
                    rx += int(cols[0])
                    tx += int(cols[8])
        return rx, tx

    def sample(self) -> tuple[float, float]:
        """(down, up) in bytes/s."""
        now = time.monotonic()
        rx, tx = self._read()
        t0, rx0, tx0 = self._last
        self._last = (now, rx, tx)
        dt = max(now - t0, 1e-3)
        return (rx - rx0) / dt, (tx - tx0) / dt


def gpu_busy() -> float | None:
    for path in Path("/sys/class/drm").glob("card[0-9]/device/gpu_busy_percent"):
        try:
            return float(path.read_text())
        except (OSError, ValueError):
            continue
    return None


def disk(path: str = "/") -> tuple[float, float]:
    """(free_gib, total_gib)"""
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize / 1073741824.0, st.f_blocks * st.f_frsize / 1073741824.0


def uptime() -> float:
    with open("/proc/uptime") as fh:
        return float(fh.read().split()[0])


def human_rate(bps: float) -> str:
    for unit in ("B", "K", "M", "G"):
        if bps < 1000 or unit == "G":
            return f"{bps:.0f}{unit}" if unit == "B" or bps >= 100 else f"{bps:.1f}{unit}"
        bps /= 1000.0
    return f"{bps:.0f}G"


def human_duration(seconds: float) -> str:
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"
