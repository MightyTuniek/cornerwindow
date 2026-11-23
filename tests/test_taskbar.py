"""Hiding the taskbar ([experimental] hide_taskbar) and the window list behind the Apps card (pure logic)."""

from __future__ import annotations

from cornerwindow.platform.desktop import taskbar as tb
from cornerwindow.platform.window.tasklist import cycle_target, desktop_keys, group_order, pick_app


class FakeSettings:
    def __init__(self, values: dict) -> None:
        self.values = dict(values)
        self.writes: list[str] = []

    def has(self, key: str) -> bool:
        return key in self.values

    def get(self, key: str):
        return self.values[key]

    def set(self, key: str, value) -> None:
        self.values[key] = value
        self.writes.append(key)


USER = {  # someone's own Zorin Taskbar settings (an older version without show-in-fullscreen)
    "intellihide": False,
    "intellihide-hide-from-windows": True,
    "intellihide-hide-from-monitor-windows": False,
    "intellihide-use-pointer": True,
    "intellihide-show-on-notification": True,
    "intellihide-only-secondary": False,
    "intellihide-persisted-state": -1,
    "panel-size": 48,
}


def test_hide_then_restore_puts_back_exactly_the_users_values(tmp_path):
    store = tb.SavedState(tmp_path / "taskbar.json")
    settings = FakeSettings(USER)
    panel = tb.IntellihidePanel("zorin-taskbar@zorinos.com", "org.x", "Zorin Taskbar", settings)
    panel.hide(store)
    assert settings.values["intellihide"] is True
    assert settings.values["intellihide-use-pointer"] is False
    assert settings.values["intellihide-hide-from-windows"] is False
    assert "intellihide-show-in-fullscreen" not in settings.values  # missing keys are skipped
    assert settings.values["panel-size"] == 48  # nothing else is touched
    saved = store.load()
    assert saved["uuid"] == "zorin-taskbar@zorinos.com" and "panel-size" not in saved["values"]
    tb.IntellihidePanel.restore(saved, settings)
    assert settings.values == USER


def test_hiding_again_keeps_the_first_saved_values(tmp_path):
    """After a crash the taskbar is still hidden: re-hiding must not save the hidden values."""
    store = tb.SavedState(tmp_path / "taskbar.json")
    settings = FakeSettings(USER)
    panel = tb.IntellihidePanel("zorin-taskbar@zorinos.com", "org.x", "Zorin Taskbar", settings)
    panel.hide(store)
    settings.writes.clear()
    panel.hide(store)
    assert settings.writes == []  # already hidden: no writes
    assert store.load()["values"]["intellihide"] is False


def test_service_show_restores_and_forgets(tmp_path, monkeypatch):
    store = tb.SavedState(tmp_path / "taskbar.json")
    settings = FakeSettings(USER)
    panel = tb.IntellihidePanel("zorin-taskbar@zorinos.com", "org.x", "Zorin Taskbar", settings)
    service = tb.Taskbar(store)
    monkeypatch.setattr(service, "detect", lambda: panel)
    monkeypatch.setattr(tb, "_panel_settings", lambda uuid, schema: settings)
    assert service.hide() == "Zorin Taskbar"
    assert service.is_hidden() and "hidden by CornerWindow" in service.describe()
    assert service.show() is True
    assert settings.values == USER and not service.is_hidden()
    assert service.show() is False  # nothing left to restore


def test_cli_show_without_anything_hidden(tmp_path):
    service = tb.Taskbar(tb.SavedState(tmp_path / "taskbar.json"))
    output, status = tb.command(service, ["show"])
    assert status == 0 and "wasn't hidden" in output
    assert tb.command(service, ["bogus"])[1] == 2


def test_broken_saved_file_is_ignored(tmp_path):
    path = tmp_path / "taskbar.json"
    path.write_text("{not json")
    assert tb.SavedState(path).load() is None


def test_desktop_keys():
    assert desktop_keys("app.zen_browser.zen.desktop") == ["app.zen_browser.zen", "zen"]
    assert desktop_keys("firefox.desktop") == ["firefox"]


def test_pick_app_prefers_wm_class_over_name():
    wmclass, ids = {"zen": "zen-app"}, {"zen": "other", "com.anthropic.claude": "claude-app"}
    assert pick_app(["zen", "Navigator"], [wmclass, ids]) == "zen-app"
    assert pick_app(["com.anthropic.Claude"], [wmclass, ids]) == "claude-app"
    assert pick_app(["unknown", ""], [wmclass, ids]) is None


def test_cycle_target_wraps():
    assert cycle_target([1, 2, 3], 3, 1) == 1
    assert cycle_target([1, 2, 3], 1, -1) == 3
    assert cycle_target([1, 2, 3], None, 1) == 1
    assert cycle_target([1, 2, 3], None, -1) == 3
    assert cycle_target([], None, 1) is None


def test_group_order_favourites_first_then_by_appearance():
    seen = ["claude", "zen", "terminal", "closed-app"]
    order = group_order(seen, ["nautilus", "zen"], running={"claude", "zen", "terminal"})
    assert order == ["nautilus", "zen", "claude", "terminal"]
