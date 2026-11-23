"""When to warn about the system: a condition must hold for `sustain` seconds, and the same
warning doesn't repeat within `cooldown` seconds. Pure logic (no GTK), so the tests drive it."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Rules:
    cpu: float = 90.0  # % busy, sustained
    temperature: float = 90.0  # °C
    memory: float = 92.0  # % used
    disk_gb: float = 5.0  # free GB on the watched disk
    sustain: float = 60.0  # seconds a CPU/memory/temperature condition must last
    cooldown: float = 1800.0  # seconds before the same alert repeats
    _since: dict = field(default_factory=dict)
    _sent: dict = field(default_factory=dict)

    @classmethod
    def from_section(cls, section: dict) -> "Rules":
        out = cls()
        for key in ("cpu", "temperature", "memory", "disk_gb", "sustain", "cooldown"):
            if key in section:
                setattr(out, key, float(section[key]))
        return out

    def check(self, now: float, cpu: float | None = None, temperature: float | None = None,
              memory: float | None = None, disk_free_gb: float | None = None) -> list[tuple[str, str, str]]:
        """-> [(id, title, body)] to show now."""
        out = []
        conditions = [
            ("cpu", cpu is not None and self.cpu > 0 and cpu >= self.cpu, True,
             "The CPU has been busy", f"{cpu:.0f}% for over {self.sustain / 60:.0f} min" if cpu is not None else ""),
            ("temperature", temperature is not None and self.temperature > 0 and temperature >= self.temperature, True,
             "Running hot", f"{temperature:.0f}°C" if temperature is not None else ""),
            ("memory", memory is not None and self.memory > 0 and memory >= self.memory, True,
             "Memory is nearly full", f"{memory:.0f}% used" if memory is not None else ""),
            ("disk", disk_free_gb is not None and self.disk_gb > 0 and disk_free_gb < self.disk_gb, False,
             "The disk is nearly full", f"{disk_free_gb:.1f} GB free" if disk_free_gb is not None else ""),
        ]
        for key, active, sustained, title, body in conditions:
            if not active:
                self._since.pop(key, None)
                continue
            start = self._since.setdefault(key, now)
            if sustained and now - start < self.sustain:
                continue
            if now - self._sent.get(key, -1e18) < self.cooldown:
                continue
            self._sent[key] = now
            out.append((key, title, body))
        return out
