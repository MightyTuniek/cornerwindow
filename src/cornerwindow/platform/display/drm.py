"""Discover connected monitors through /sys/class/drm (no root needed)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

DRM = Path("/sys/class/drm")


@dataclass
class Connector:
    name: str  # "HDMI-A-1"
    card: str  # "card1"
    i2c_bus: int | None  # DDC channel, e.g. 4 -> /dev/i2c-4
    model: str  # from EDID, e.g. "Acer VG240Y"
    internal: bool  # eDP/LVDS laptop panel


def parse_edid(edid: bytes) -> str:
    """Return a human readable monitor name from raw EDID bytes."""
    if len(edid) < 128 or edid[:8] != b"\x00\xff\xff\xff\xff\xff\xff\x00":
        return ""
    vendor_raw = (edid[8] << 8) | edid[9]
    vendor = "".join(chr(((vendor_raw >> shift) & 0x1F) + 64) for shift in (10, 5, 0))
    name = ""
    for offset in range(54, 126, 18):
        block = edid[offset:offset + 18]
        if block[0] == 0 and block[1] == 0 and block[3] == 0xFC:
            name = block[5:18].split(b"\n")[0].decode("ascii", "replace").strip()
    if name:
        return name
    product = edid[10] | (edid[11] << 8)
    return f"{vendor} {product:04X}"


def connectors() -> list[Connector]:
    found: list[Connector] = []
    for path in sorted(DRM.glob("card*-*")):
        match = re.match(r"(card\d+)-(.+)", path.name)
        if not match:
            continue
        try:
            if (path / "status").read_text().strip() != "connected":
                continue
        except OSError:
            continue
        card, name = match.groups()
        bus = None
        ddc = path / "ddc"
        if ddc.exists():
            bus_match = re.search(r"i2c-(\d+)$", str(ddc.resolve()))
            if bus_match:
                bus = int(bus_match.group(1))
        if bus is None:  # some drivers expose the bus as a child directory instead
            for child in path.glob("i2c-*"):
                bus = int(child.name.split("-")[1])
                break
        try:
            model = parse_edid((path / "edid").read_bytes())
        except OSError:
            model = ""
        internal = name.startswith(("eDP", "LVDS", "DSI"))
        found.append(Connector(name, card, bus, model or name, internal))
    return found


def monitor_models() -> list[str]:
    return [c.model for c in connectors()]
