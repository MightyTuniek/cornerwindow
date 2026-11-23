"""System at a glance: CPU, GPU, memory and network in one compact row; click for the full view
with sparklines, disk, uptime and the busiest processes (stop one from there)."""

from __future__ import annotations

from collections import deque

from gi.repository import Gtk

from ..core.activity import BLUE, GREEN, ORANGE, PURPLE
from ..platform import processes, sensors
from ..ui.widgets import Card, IconButton, Sparkline, label
from .base import Module, module


class Metric(Gtk.Box):
    def __init__(self, title: str, color, maximum: float | None = 100.0, compact: bool = False,
                 data: deque | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        head = Gtk.Box(spacing=4)
        head.pack_start(label(title, "cw-title"), False, False, 0)
        self.sub = label("", "cw-metric-sub", xalign=1.0)
        if not compact:
            head.pack_end(self.sub, False, False, 0)
        self.pack_start(head, False, False, 0)
        self.value = label("–", "cw-metric-small" if compact else "cw-metric-value")
        self.pack_start(self.value, False, False, 0)
        self.spark = Sparkline(color=color, maximum=maximum, height=12 if compact else 22, data=data)
        self.pack_start(self.spark, False, False, 2)

    def update(self, value: str, sub: str = "") -> None:
        """The value changed; the shared history already holds the new point."""
        self.value.set_text(value)
        self.sub.set_text(sub)
        self.spark.redraw()


@module("glance", title="System", icon="monitor", description="CPU, GPU, memory, network and disk")
class Glance(Module):
    def setup(self) -> None:
        self.cpu = sensors.CpuUsage()
        self.net = sensors.NetSpeed()
        self.procs = processes.ProcessSampler()
        # history lives here, not in the widgets: it survives rebuilds and the compact/full switch
        self.history = {key: deque(maxlen=40) for key in ("cpu", "gpu", "mem", "net")}

    def configure(self) -> None:
        self.cpu_temp = sensors.find_sensor(self.config.get("cpu_sensor", "auto"), "cpu")
        self.gpu_temp = sensors.find_sensor(self.config.get("gpu_sensor", "auto"), "gpu")
        self.disk_path = self.config.get("disk_path", "/")

    def build(self):
        card = Card()
        # The compact row and the full view share the samples; a click switches between them.
        self.stack = Gtk.Stack()
        self.stack.set_vhomogeneous(False)
        self.stack.set_interpolate_size(True)
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(200)

        row = Gtk.Grid(column_spacing=10, column_homogeneous=True)
        h = self.history
        self.c_cpu = Metric("CPU", BLUE, compact=True, data=h["cpu"])
        self.c_gpu = Metric("GPU", PURPLE, compact=True, data=h["gpu"])
        self.c_mem = Metric("Memory", GREEN, compact=True, data=h["mem"])
        self.c_net = Metric("Network", ORANGE, maximum=None, compact=True, data=h["net"])
        for i, metric in enumerate((self.c_cpu, self.c_gpu, self.c_mem, self.c_net)):
            row.attach(metric, i, 0, 1, 1)
        self.stack.add_named(_clickable(row, self.toggle, "Click for more"), "compact")

        full = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        grid = Gtk.Grid(column_spacing=14, row_spacing=8, column_homogeneous=True)
        self.m_cpu = Metric("CPU", BLUE, data=h["cpu"])
        self.m_gpu = Metric("GPU", PURPLE, data=h["gpu"])
        self.m_mem = Metric("Memory", GREEN, data=h["mem"])
        self.m_net = Metric("Network", ORANGE, maximum=None, data=h["net"])
        grid.attach(self.m_cpu, 0, 0, 1, 1)
        grid.attach(self.m_gpu, 1, 0, 1, 1)
        grid.attach(self.m_mem, 0, 1, 1, 1)
        grid.attach(self.m_net, 1, 1, 1, 1)
        full.pack_start(_clickable(grid, self.toggle, "Click for less"), False, False, 0)
        self.footer = label("", "cw-metric-sub")
        full.pack_start(self.footer, False, False, 0)
        self.proc_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        full.pack_start(self.proc_box, False, False, 0)
        self.stack.add_named(full, "full")
        card.pack_start(self.stack, False, False, 0)
        compact = bool(self.config.get("compact", True))
        expanded = bool(self.ctx.state.get("glance.expanded", not compact))
        self.stack.set_visible_child_name("full" if expanded else "compact")
        self.ctx.every(float(self.config.get("interval", 1.0)), self.sample)
        return card

    def toggle(self) -> None:
        expanded = self.stack.get_visible_child_name() != "full"
        self.stack.set_visible_child_name("full" if expanded else "compact")
        self.ctx.state.set("glance.expanded", expanded)
        if expanded:
            self.sample()

    def sample(self) -> None:
        h = self.history
        cpu = self.cpu.sample()
        temp = self.cpu_temp.read() if self.cpu_temp else None
        h["cpu"].append(max(cpu, 0.0))
        for m in (self.m_cpu, self.c_cpu):
            m.update(f"{cpu:.0f}%", f"{temp:.0f}°C" if temp is not None else "")

        busy = sensors.gpu_busy()
        gtemp = self.gpu_temp.read() if self.gpu_temp else None
        if busy is not None or gtemp is not None:
            h["gpu"].append(max(busy if busy is not None else gtemp, 0.0))
        for m in (self.m_gpu, self.c_gpu):
            if busy is not None:
                m.update(f"{busy:.0f}%", f"{gtemp:.0f}°C" if gtemp is not None else "")
            elif gtemp is not None:
                m.update(f"{gtemp:.0f}°C", "")

        used, total = sensors.memory()
        h["mem"].append(100 * used / max(total, 0.1))
        for m in (self.m_mem, self.c_mem):
            m.update(f"{used:.1f} GB", f"of {total:.0f}")

        down, up = self.net.sample()
        h["net"].append(max(down, 0.0))
        self.m_net.update(f"↓ {sensors.human_rate(down)}", f"↑ {sensors.human_rate(up)}")
        self.c_net.update(f"↓ {sensors.human_rate(down)}", "")

        if self.stack.get_visible_child_name() == "full":
            free, disk_total = sensors.disk(self.disk_path)
            self.footer.set_text(f"Disk {free:.0f} GB free of {disk_total:.0f} · "
                                 f"up {sensors.human_duration(sensors.uptime())}")
            self._show_processes()

    # -- busiest processes (full view) ------------------------------------------------
    def _show_processes(self) -> None:
        count = int(self.config.get("processes", 4))
        for child in self.proc_box.get_children():
            self.proc_box.remove(child)
            child.destroy()
        if count <= 0:
            return
        for proc in self.procs.top(count):
            line = Gtk.Box(spacing=6)
            line.pack_start(label(proc.name, "cw-small", ellipsize=True), True, True, 0)
            line.pack_start(label(f"{proc.cpu:.0f}%", "cw-dim cw-small cw-tnum"), False, False, 0)
            line.pack_start(label(f"{proc.rss_mb:.0f} MB", "cw-dim cw-small cw-tnum"), False, False, 0)
            stop = IconButton("close", lambda p=proc: self._stop(p), f"Stop {proc.name} ({proc.pid})",
                              "cw-icon cw-plain cw-mini", 12)
            stop.set_sensitive(proc.user)
            line.pack_start(stop, False, False, 0)
            self.proc_box.pack_start(line, False, False, 0)
        self.proc_box.show_all()

    def _stop(self, proc) -> None:
        self.ctx.activity.show("glance.stop", dot=True, color=ORANGE, timeout=0.9)
        processes.stop(proc.pid)
        self.sample()


def _clickable(child: Gtk.Widget, on_click, tooltip: str) -> Gtk.Widget:
    box = Gtk.EventBox()
    box.set_visible_window(False)
    box.add(child)
    box.set_tooltip_text(tooltip)
    box.connect("button-release-event", lambda _w, e: (e.button == 1 and on_click()) or False)
    return box
