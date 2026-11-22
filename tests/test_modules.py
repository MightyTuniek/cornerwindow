"""Module system tests (needs PyGObject, no display server)."""

from __future__ import annotations

import pytest

gi = pytest.importorskip("gi")

from cornerwindow.modules import registry  # noqa: E402
from cornerwindow.modules.base import Command, Module, module, registered  # noqa: E402
from cornerwindow.modules.timer import _fmt, _parse_duration  # noqa: E402


def test_decorator_registers():
    @module("test_dummy", title="Dummy")
    class Dummy(Module):
        def build(self):
            return None

    assert registered()["test_dummy"] is Dummy
    assert Dummy.title == "Dummy" and Dummy.area == "main"


def test_user_modules_load_from_folder(tmp_path):
    (tmp_path / "hello.py").write_text(
        "from cornerwindow.modules.base import Module, module\n"
        "@module('hello_user', title='Hello')\n"
        "class Hello(Module):\n"
        "    def build(self):\n"
        "        return None\n"
    )
    assert registry.load_user_modules([tmp_path]) == ["hello"]
    assert "hello_user" in registered()


def test_broken_user_module_does_not_crash(tmp_path):
    (tmp_path / "broken.py").write_text("raise RuntimeError('boom')\n")
    assert registry.load_user_modules([tmp_path]) == []


def test_command_matching():
    cmd = Command(("b", "brightness"), "Brightness")
    assert cmd.matches("b") and cmd.matches("bri") and not cmd.matches("x")


@pytest.mark.parametrize("text,seconds", [("10", 600), ("90s", 90), ("1h30m", 5400), ("1.5", 90)])
def test_timer_durations(text, seconds):
    assert _parse_duration(text) == seconds


def test_timer_format():
    assert _fmt(65) == "1:05"
    assert _fmt(3725) == "1:02:05"
    assert _parse_duration("soon") is None
