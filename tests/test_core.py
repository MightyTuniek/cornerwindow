"""Tests for the pure-Python pieces: config layering, springs, shape, calculator, parsers."""

from __future__ import annotations

import math
import tomllib
from pathlib import Path

import pytest

from cornerwindow import config
from cornerwindow.core import calc
from cornerwindow.core.anim import Spring, rubber_band
from cornerwindow.platform.audio.wpctl import parse_status, parse_volume
from cornerwindow.platform.display import ddc_i2c
from cornerwindow.platform.display.ddcutil import parse_detect, parse_getvcp
from cornerwindow.platform.display.drm import parse_edid
from cornerwindow.platform.display.base import Control
from cornerwindow.ui import shape

REPO = Path(__file__).resolve().parent.parent


# ── config ───────────────────────────────────────────────────────────────────
def test_deep_merge_replaces_lists_and_merges_tables():
    base = {"a": {"x": 1, "y": [1, 2]}, "b": 1}
    out = config.deep_merge(base, {"a": {"y": [3]}, "c": 2})
    assert out == {"a": {"x": 1, "y": [3]}, "b": 1, "c": 2}
    assert base["a"]["y"] == [1, 2]  # input untouched


def test_machine_match_by_hostname_or_monitor():
    match = {"hostname": ["box"], "monitor_model": ["VG240Y"]}
    assert config.machine_matches(match, {"hostname": "box", "monitor_models": []})
    assert config.machine_matches(match, {"hostname": "x", "monitor_models": ["Acer VG240Y"]})
    assert not config.machine_matches(match, {"hostname": "x", "monitor_models": ["Dell U2720Q"]})
    assert not config.machine_matches({}, {"hostname": "box"})


def test_layering(tmp_path):
    (tmp_path / "machines").mkdir()
    (tmp_path / "default.toml").write_text('[general]\ncorner = "top-right"\nmonitor = "primary"\n')
    (tmp_path / "machines" / "pc.toml").write_text('[match]\nhostname = ["pc"]\n[general]\ncorner = "top-left"\n')
    user = tmp_path / "user.toml"
    user.write_text('[general]\nmonitor = 1\n')
    cfg = config.load({"hostname": "pc", "monitor_models": []}, tmp_path, user)
    assert cfg.get("general.corner") == "top-left"
    assert cfg.get("general.monitor") == 1
    assert cfg.get("missing.key", "d") == "d"
    assert len(cfg.sources) == 3


def test_shipped_configs_parse():
    # defaults alone, with facts that match no machine file
    cfg = config.load({"hostname": "no-such-host", "monitor_models": []},
                      REPO / "config", REPO / "nonexistent.toml")
    assert cfg.get("general.corner") == "top-right"
    assert [p["name"] for p in cfg.get("display.presets")] == ["Day", "Evening", "Night", "Movie"]
    assert all(isinstance(col, list) for col in cfg.get("modules.columns"))
    # every local machine file (config/machines/*.toml, not tracked in git) parses and applies
    for machine in sorted((REPO / "config" / "machines").glob("*.toml")):
        match = config._read(machine).get("match", {})
        hosts = match.get("hostname", [])
        hosts = [hosts] if isinstance(hosts, str) else hosts
        if not hosts:
            continue
        cfg = config.load({"hostname": hosts[0], "monitor_models": []},
                          REPO / "config", REPO / "nonexistent.toml")
        assert str(machine) in [str(s) for s in cfg.sources], machine
        limits = cfg.get("display.limits") or {}
        # presets must never exceed the machine's own ceiling
        for p in cfg.get("display.presets"):
            for key, ceiling in limits.items():
                assert p.get(key, 0) <= ceiling, (machine.name, p["name"], key)


# ── springs ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("damping", [0.6, 0.78, 1.0])
def test_spring_converges(damping):
    spring = Spring(0.0, response=0.4, damping_fraction=damping)
    spring.to(100.0)
    elapsed = 0.0
    while spring.step(1 / 75) and elapsed < 5:
        elapsed += 1 / 75
    assert spring.value == 100.0 and spring.velocity == 0.0
    assert elapsed < 2.0


def test_underdamped_spring_overshoots_critically_damped_does_not():
    def peak(damping):
        s = Spring(0.0, response=0.4, damping_fraction=damping).to(1.0)
        top = 0.0
        for _ in range(300):
            s.step(1 / 120)
            top = max(top, s.value)
        return top

    assert peak(0.6) > 1.05
    assert peak(1.0) <= 1.0005


def test_spring_keeps_velocity_when_retargeted():
    s = Spring(0.0, response=0.4, damping_fraction=0.8).to(1.0)
    for _ in range(10):
        s.step(1 / 75)
    moving = s.velocity
    s.to(0.0)
    assert s.velocity == moving  # no snap: reversal is continuous


def test_rubber_band_resists():
    assert rubber_band(0, 100) == 0
    assert 0 < rubber_band(50, 100) < 50
    assert rubber_band(-50, 100) == -rubber_band(50, 100)
    assert rubber_band(10_000, 100) < 100


# ── shape ────────────────────────────────────────────────────────────────────
def _points(ops):
    pts = []
    for op in ops:
        if op[0] in ("M", "L"):
            pts.append(op[1:3])
        elif op[0] == "C":
            pts.append(op[5:7])
    return pts


def test_collapsed_shape_is_a_quarter_circle():
    ops = shape.outline(shape.ShapeParams(20, 20, 20, ears=0, smoothing=0.6))
    curves = [op for op in ops if op[0] == "C"]
    assert len(curves) == 1  # one arc, no smoothing beziers
    _, x1, y1, x2, y2, x3, y3 = curves[0]
    assert (round(x3, 6), round(y3, 6)) == (0.0, 20.0)
    k = 4 / 3 * math.tan(math.pi / 8) * 20  # standard quarter-circle control distance
    assert math.isclose(y1, k, abs_tol=1e-6) or math.isclose(x1, 20 - 0, abs_tol=1e-6)


def test_open_shape_stays_inside_its_box_plus_ears():
    params = shape.ShapeParams(400, 300, 28, ears=10, smoothing=0.6)
    for x, y in _points(shape.outline(params)):
        assert -1e-6 <= x <= 410 + 1e-6
        assert -1e-6 <= y <= 310 + 1e-6


def test_negative_ears_round_into_the_panel():
    params = shape.ShapeParams(400, 300, 28, ears=-14, smoothing=0.6)
    pts = _points(shape.outline(params))
    for x, y in pts:
        assert -1e-6 <= x <= 400 + 1e-6 and -1e-6 <= y <= 300 + 1e-6
    # the panel no longer reaches the screen edges at its far corners
    assert (400.0, 0.0) not in pts and (0.0, 300.0) not in pts
    assert (386.0, 0.0) in pts and (0.0, 286.0) in pts


def test_corner_transform():
    assert shape.corner_transform("top-right", 100, 50) == (100, 0, -1, 1)
    assert shape.corner_transform("bottom-left", 100, 50) == (0, 50, 1, -1)


def test_quarter_circle_rows_cover_the_disc():
    rows = shape.quarter_circle_rows(20, step=2)
    assert rows[0][2] == 20  # first row is full width
    assert rows[-1][2] < 10  # last row is narrow
    assert sum(h for *_xy, _w, h in rows) == 20


# ── DDC/CI ───────────────────────────────────────────────────────────────────
def test_ddc_set_packet():
    packet = ddc_i2c.encode_set(0x10, 70)
    assert packet[:6] == bytes([0x51, 0x84, 0x03, 0x10, 0x00, 70])
    assert packet[6] == ddc_i2c.checksum(0x6E, packet[:6])


def test_ddc_get_packet():
    # Known-good value from the DDC/CI spec example: get brightness
    assert ddc_i2c.encode_get(0x10) == bytes([0x51, 0x82, 0x01, 0x10, 0xAC])


def _reply(code, current, maximum, result=0):
    body = bytes([0x6E, 0x88, 0x02, result, code, 0x00, maximum >> 8, maximum & 0xFF,
                  current >> 8, current & 0xFF])
    return body + bytes([ddc_i2c.checksum(0x50, body)])


def test_ddc_reply_decoding():
    assert ddc_i2c.decode_get_reply(0x10, _reply(0x10, 42, 100)) == (42, 100)
    with pytest.raises(ddc_i2c.DisplayError):
        ddc_i2c.decode_get_reply(0x10, _reply(0x10, 42, 100, result=1))
    bad = bytearray(_reply(0x12, 1, 100))
    bad[-1] ^= 0xFF
    with pytest.raises(ddc_i2c.DisplayError):
        ddc_i2c.decode_get_reply(0x12, bytes(bad))


def test_control_percent_mapping():
    ctl = Control("brightness", 50, 200)
    assert ctl.percent == 25
    assert ctl.raw_from_percent(50) == 100
    assert ctl.raw_from_percent(150) == 200


def test_edid_name():
    edid = bytearray(128)
    edid[:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
    edid[8], edid[9] = 0x04, 0x72  # "ACR"
    edid[54:57] = b"\x00\x00\x00"
    edid[57] = 0xFC
    edid[59:72] = b"Acer VG240Y\n "
    assert parse_edid(bytes(edid)) == "Acer VG240Y"


def test_ddcutil_parsing():
    detect = ("Display 1\n   I2C bus:  /dev/i2c-4\n   DRM connector:           card1-HDMI-A-1\n"
              "   Monitor:                 ACR:Acer VG240Y:T1234\n")
    assert parse_detect(detect) == [(4, "Acer VG240Y", "HDMI-A-1")]
    assert parse_getvcp("VCP 10 C 55 100\n") == (55, 100)


# ── audio ────────────────────────────────────────────────────────────────────
WPCTL_STATUS = """\
PipeWire 'pipewire-0' [1.0.5, user@host, cookie:1]
 └─ Clients:
        31. pipewire                            [1.0.5, user@host, pid:1]

Audio
 ├─ Devices:
 │      48. Family 17h HD Audio Controller      [alsa]
 │
 ├─ Sinks:
 │  *   53. Family 17h HD Audio Controller Analog Stereo [vol: 1.00]
 │      55. Ellesmere HDMI Audio Digital Stereo (HDMI 4) [vol: 0.40]
 │
 ├─ Sources:
 │  *   54. Family 17h HD Audio Controller Analog Stereo [vol: 1.00]
 │
 └─ Streams:

Video
 ├─ Devices:
"""


def test_wpctl_status_parsing():
    sinks = parse_status(WPCTL_STATUS, "sink")
    assert [(d.id, d.default) for d in sinks] == [("53", True), ("55", False)]
    assert sinks[1].name.startswith("Ellesmere HDMI")
    assert [d.id for d in parse_status(WPCTL_STATUS, "source")] == ["54"]


def test_wpctl_volume():
    assert parse_volume("Volume: 0.40\n").volume == 0.4
    assert parse_volume("Volume: 0.40 [MUTED]\n").muted


# ── calculator ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("expr,value", [("2*(3+4)", 14), ("2^10", 1024), ("=sqrt(16)", 4), ("10/4", 2.5)])
def test_calc(expr, value):
    assert calc.looks_like_math(expr)
    assert calc.evaluate(expr) == value


def test_calc_rejects_code_and_commands():
    assert not calc.looks_like_math("b 40")
    with pytest.raises(ValueError):
        calc.evaluate("__import__('os')")
    with pytest.raises(ValueError):
        calc.evaluate("9**99999")


# ── brightness ceiling ───────────────────────────────────────────────────────
def test_display_limit_clamps_every_write():
    from cornerwindow.core.bus import EventBus
    from cornerwindow.platform.display.service import DisplayService

    svc = DisplayService({"limits": {"brightness": 30}}, EventBus())
    svc._writer.submit = lambda *_a: None  # no hardware
    svc._percent = {"brightness": 0.0, "contrast": 0.0}
    assert svc.set_percent("brightness", 75) == 30
    assert svc.step("brightness", +50) == 30
    assert svc.set_percent("brightness", -5) == 0
    assert svc.set_percent("contrast", 75) == 75  # no limit configured for contrast
    assert DisplayService({"limits": {"brightness": "junk"}}, EventBus()).limit("brightness") == 100


# ── config writing (settings window) ─────────────────────────────────────────
from cornerwindow.config_edit import TomlDocument, UserConfigWriter, dump_value  # noqa: E402

SAMPLE = '''\
# my settings
[trigger]
radius = 20          # small
scroll = "display.brightness.step"

[modules]
columns = [
  ["a", "b"],
  ["c"],
]
drawer = ["timer"]   # tabs

[[display.presets]]
name = "Night"
brightness = 0
'''


def test_document_edits_keep_comments_and_layout():
    doc = TomlDocument(SAMPLE)
    doc.set("trigger", "radius", 24)
    doc.set("modules", "columns", [["x"]])
    doc.set("trigger", "middle_click", "audio.mute")
    doc.set("general", "corner", "top-left")
    text = doc.text()
    data = tomllib.loads(text)
    assert data["trigger"] == {"radius": 24, "scroll": "display.brightness.step", "middle_click": "audio.mute"}
    assert data["modules"] == {"columns": [["x"]], "drawer": ["timer"]}
    assert data["general"]["corner"] == "top-left"
    assert "# my settings" in text and "# small" in text and "# tabs" in text
    assert data["display"]["presets"] == [{"name": "Night", "brightness": 0}]


def test_array_of_tables_replace_and_remove():
    doc = TomlDocument(SAMPLE)
    doc.set_array_of_tables("display.presets", [{"name": "A", "brightness": 1}, {"name": "B", "brightness": 2}])
    assert [p["name"] for p in tomllib.loads(doc.text())["display"]["presets"]] == ["A", "B"]
    doc.set_array_of_tables("display.presets", [])
    assert "display" not in tomllib.loads(doc.text())


def test_dump_value_roundtrip():
    for value in (True, 3, 0.42, "a \"quoted\" \\ path", [1, 2], [["a"], ["b", "c"]],
                  {"brightness": 30, "odd key": "x"}, {}):
        assert tomllib.loads(f"v = {dump_value(value)}")["v"] == value


def test_writer_only_keeps_differences(tmp_path):
    writer = UserConfigWriter(tmp_path / "config.toml")
    writer.set("trigger", "radius", 24, base_value=20)
    assert tomllib.loads(writer.read_text())["trigger"]["radius"] == 24
    writer.set("trigger", "radius", 20, base_value=20)  # back to default -> removed
    assert "trigger" not in tomllib.loads(writer.read_text())
    writer.set("display", "presets", [{"name": "N", "brightness": 0}], base_value=[])
    assert tomllib.loads(writer.read_text())["display"]["presets"][0]["name"] == "N"
    writer.reset("display", "presets")
    assert "display" not in tomllib.loads(writer.read_text())


def test_writer_refuses_to_write_broken_toml(tmp_path):
    writer = UserConfigWriter(tmp_path / "config.toml")
    with pytest.raises(tomllib.TOMLDecodeError):
        writer.write_text("[broken\n")
    assert not (tmp_path / "config.toml").exists()


def test_origin_tracking(tmp_path):
    (tmp_path / "machines").mkdir()
    (tmp_path / "default.toml").write_text("[a]\nx = 1\ny = 1\nz = 1\n")
    (tmp_path / "machines" / "pc.toml").write_text('[match]\nhostname = ["pc"]\n[a]\ny = 2\n')
    user = tmp_path / "user.toml"
    user.write_text("[a]\nz = 3\n")
    cfg = config.load({"hostname": "pc", "monitor_models": []}, tmp_path, user)
    assert (cfg.origin("a", "x"), cfg.origin("a", "y"), cfg.origin("a", "z")) == ("default", "machine", "yours")
    assert cfg.base["a"]["z"] == 1  # what reset returns to
