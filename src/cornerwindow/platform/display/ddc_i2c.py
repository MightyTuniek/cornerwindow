"""Native DDC/CI over Linux i2c-dev, in pure Python.

This talks the VESA DDC/CI protocol directly (what ddcutil does), which is
several times faster than spawning ddcutil and needs no extra packages.
It needs read/write access to /dev/i2c-N; install.sh adds a udev rule for that.

Protocol summary (VESA DDC/CI 1.1):
  host -> display, I2C address 0x37:
    [0x51, 0x80 | len, payload..., checksum]
    checksum = XOR of 0x6E (our write address) and every byte before it
  Set VCP feature: payload = [0x03, code, value_hi, value_lo]
  Get VCP feature: payload = [0x01, code]; after >=40ms read 11 bytes:
    [0x6E, 0x88, 0x02, result, code, type, max_hi, max_lo, cur_hi, cur_lo, checksum]
    reply checksum = XOR of 0x50 and bytes[0:10]
"""

from __future__ import annotations

import fcntl
import os
import threading
import time

from ...core import log
from .base import VCP_CODES, Control, DisplayBackend, DisplayError, Monitor
from .drm import connectors

_log = log.get("ddc")

I2C_SLAVE = 0x0703
DDC_ADDR = 0x37
HOST_WRITE_ADDR = 0x6E
REPLY_XOR_SEED = 0x50
SOURCE_ADDR = 0x51

# Monitors need a breather between commands (spec says 50ms; most cope with less).
WRITE_DELAY = 0.050
READ_DELAY = 0.045


def checksum(seed: int, data: bytes | bytearray | list[int]) -> int:
    value = seed
    for byte in data:
        value ^= byte
    return value


def encode_set(code: int, value: int) -> bytes:
    body = bytes([SOURCE_ADDR, 0x84, 0x03, code, (value >> 8) & 0xFF, value & 0xFF])
    return body + bytes([checksum(HOST_WRITE_ADDR, body)])


def encode_get(code: int) -> bytes:
    body = bytes([SOURCE_ADDR, 0x82, 0x01, code])
    return body + bytes([checksum(HOST_WRITE_ADDR, body)])


def decode_get_reply(code: int, reply: bytes) -> tuple[int, int]:
    """Return (current, maximum) from a Get VCP reply, or raise DisplayError."""
    if len(reply) < 11:
        raise DisplayError(f"short DDC reply ({len(reply)} bytes)")
    if reply[0] != HOST_WRITE_ADDR or (reply[1] & 0x80) == 0:
        raise DisplayError(f"unexpected DDC reply header {reply[:2].hex()}")
    length = reply[1] & 0x7F
    if length != 8 or reply[2] != 0x02:
        raise DisplayError(f"not a VCP reply: {reply.hex()}")
    if checksum(REPLY_XOR_SEED, reply[:10]) != reply[10]:
        raise DisplayError("DDC reply checksum mismatch")
    if reply[3] != 0x00:
        raise DisplayError(f"monitor rejected VCP 0x{code:02x} (result {reply[3]})")
    if reply[4] != code:
        raise DisplayError(f"reply for wrong feature 0x{reply[4]:02x}")
    maximum = (reply[6] << 8) | reply[7]
    current = (reply[8] << 8) | reply[9]
    return current, maximum


class I2CBus:
    def __init__(self, bus: int) -> None:
        self.path = f"/dev/i2c-{bus}"
        self._lock = threading.Lock()
        self._last = 0.0

    def accessible(self) -> bool:
        return os.access(self.path, os.R_OK | os.W_OK)

    def _open(self) -> int:
        try:
            fd = os.open(self.path, os.O_RDWR)
        except OSError as exc:
            raise DisplayError(f"{self.path}: {exc.strerror}") from exc
        fcntl.ioctl(fd, I2C_SLAVE, DDC_ADDR)
        return fd

    def _pace(self) -> None:
        wait = WRITE_DELAY - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)

    def set_vcp(self, code: int, value: int) -> None:
        with self._lock:
            self._pace()
            fd = self._open()
            try:
                os.write(fd, encode_set(code, value))
            except OSError as exc:
                raise DisplayError(f"DDC write failed: {exc.strerror}") from exc
            finally:
                os.close(fd)
                self._last = time.monotonic()

    def get_vcp(self, code: int, retries: int = 3) -> tuple[int, int]:
        last_error: Exception | None = None
        with self._lock:
            for _ in range(retries):
                self._pace()
                fd = self._open()
                try:
                    os.write(fd, encode_get(code))
                    time.sleep(READ_DELAY)
                    reply = os.read(fd, 11)
                    return decode_get_reply(code, reply)
                except (OSError, DisplayError) as exc:
                    last_error = exc
                finally:
                    os.close(fd)
                    self._last = time.monotonic()
        raise DisplayError(f"DDC read of 0x{code:02x} on {self.path} failed: {last_error}")


class DdcI2cBackend(DisplayBackend):
    name = "ddc"

    def __init__(self, buses: list[int] | None = None) -> None:
        self._forced_buses = buses
        self._buses: dict[str, I2CBus] = {}

    def probe(self) -> list[Monitor]:
        monitors: list[Monitor] = []
        targets = []
        if self._forced_buses:
            names = {c.i2c_bus: c for c in connectors()}
            for bus in self._forced_buses:
                conn = names.get(bus)
                targets.append((bus, conn.model if conn else f"i2c-{bus}", conn.name if conn else ""))
        else:
            for conn in connectors():
                if conn.i2c_bus is not None and not conn.internal:
                    targets.append((conn.i2c_bus, conn.model, conn.name))

        for bus_no, model, connector in targets:
            bus = I2CBus(bus_no)
            if not bus.accessible():
                _log.info("No access to %s (run install.sh to add the i2c udev rule)", bus.path)
                continue
            controls: dict[str, Control] = {}
            for control, code in VCP_CODES.items():
                try:
                    current, maximum = bus.get_vcp(code)
                    controls[control] = Control(control, current, maximum or 100)
                except DisplayError as exc:
                    _log.debug("%s: %s", model, exc)
            if not controls:
                _log.info("%s on %s does not answer DDC/CI (enable DDC/CI in its OSD menu?)",
                          model, bus.path)
                continue
            monitor_id = f"ddc:{bus_no}"
            self._buses[monitor_id] = bus
            monitors.append(Monitor(monitor_id, model, connector, self, controls))
        return monitors

    def write(self, monitor: Monitor, control: str, raw: int) -> None:
        self._buses[monitor.id].set_vcp(VCP_CODES[control], raw)

    def read(self, monitor: Monitor, control: str) -> Control | None:
        current, maximum = self._buses[monitor.id].get_vcp(VCP_CODES[control])
        return Control(control, current, maximum or 100)
