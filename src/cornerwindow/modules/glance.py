"""System at a glance: CPU, GPU, memory, network, disk, each with a sparkline."""

from __future__ import annotations

from gi.repository import Gtk

from ..core.activity import BLUE, GREEN, ORANGE, PURPLE
from ..platform import sensors
from ..ui.widgets import Card, Sparkline, label
from .base import Module, module


class Metric(Gtk.Box):
    def __init__(self, title: str, color, maximum: float | None = 100.0) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        head = Gtk.Box(spacing=4)
        head.pack_start(label(title, "cw-title"), False, False, 0)
        self.sub = label("", "cw-metric-sub", xalign=1.0)
        head.pack_end(self.sub, False, False, 0)
        self.pack_start(head, False, False, 0)
        self.value = label("–", "cw-metric-value")
        self.pack_start(self.value, False, False, 0)
        self.spark = Sparkline(color=color, maximum=maximum, height=22)
        self.pack_start(self.spark, False, False, 2)

    def update(self, value: str, sub: str = "", point: float | None = None) -> None:
        self.value.set_text(value)
        self.sub.set_text(sub)
        if point is not None:
            self.spark.push(point)


@module("glance", title="System", icon="monitor")
class Glance(Module):
    def setup(self) -> None:
        self.cpu = sensors.CpuUsage()
        self.net = sensors.NetSpeed()

    def configure(self) -> None:
        self.cpu_temp = sensors.find_sensor(self.config.get("cpu_sensor", "auto"), "cpu")
        self.gpu_temp = sensors.find_sensor(self.config.get("gpu_sensor", "auto"), "gpu")
        self.disk_path = self.config.get("disk_path", "/")

    def build(self):
        card = Card()
        grid = Gtk.Grid(column_spacing=14, row_spacing=8, column_homogeneous=True)
        self.m_cpu = Metric("CPU", BLUE)
        self.m_gpu = Metric("GPU", PURPLE)
        self.m_mem = Metric("Memory", GREEN)
        self.m_net = Metric("Network", ORANGE, maximum=None)
        grid.attach(self.m_cpu, 0, 0, 1, 1)
        grid.attach(self.m_gpu, 1, 0, 1, 1)
        grid.attach(self.m_mem, 0, 1, 1, 1)
        grid.attach(self.m_net, 1, 1, 1, 1)
        card.pack_start(grid, False, False, 0)
        self.footer = label("", "cw-metric-sub")
        card.pack_start(self.footer, False, False, 0)
        self.ctx.every(float(self.config.get("interval", 1.0)), self.sample)
        return card

    def sample(self) -> None:
        cpu = self.cpu.sample()
        temp = self.cpu_temp.read() if self.cpu_temp else None
        self.m_cpu.update(f"{cpu:.0f}%", f"{temp:.0f}°C" if temp is not None else "", cpu)

        busy = sensors.gpu_busy()
        gtemp = self.gpu_temp.read() if self.gpu_temp else None
        if busy is not None:
            self.m_gpu.update(f"{busy:.0f}%", f"{gtemp:.0f}°C" if gtemp is not None else "", busy)
        elif gtemp is not None:
            self.m_gpu.update(f"{gtemp:.0f}°C", "", gtemp)

        used, total = sensors.memory()
        self.m_mem.update(f"{used:.1f} GB", f"of {total:.0f}", 100 * used / max(total, 0.1))

        down, up = self.net.sample()
        self.m_net.update(f"↓ {sensors.human_rate(down)}", f"↑ {sensors.human_rate(up)}", down)

        free, disk_total = sensors.disk(self.disk_path)
        self.footer.set_text(f"Disk {free:.0f} GB free of {disk_total:.0f} · up {sensors.human_duration(sensors.uptime())}")
