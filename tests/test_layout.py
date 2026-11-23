"""The card layout model behind the settings Layout page (pure logic)."""

from __future__ import annotations

from cornerwindow.modules.layout import Layout, allowed


def base() -> Layout:
    return Layout.from_config({
        "top": ["commandbar"],
        "columns": [["display", "audio", "media"], ["header", "toggles"]],
        "drawer": ["timer", "notes"],
        "hidden": [],
    })


def test_round_trip_keeps_config():
    layout = base()
    assert layout.to_config() == {
        "top": ["commandbar"],
        "columns": [["display", "audio", "media"], ["header", "toggles"]],
        "drawer": ["timer", "notes"],
        "tools": [],
        "hidden": [],
    }


def test_hiding_keeps_the_place():
    layout = base()
    layout.set_visible("audio", False)
    assert layout.where("audio") == ("col", 0)
    assert not layout.visible("audio")
    assert "audio" not in layout.shown_ids()
    layout.set_visible("audio", True)
    assert layout.columns[0] == ["display", "audio", "media"] and layout.visible("audio")


def test_switching_on_an_unplaced_card_uses_the_shortest_column():
    layout = base()
    assert layout.unplaced(["display", "weather", "battery"]) == ["weather", "battery"]
    layout.set_visible("weather", True)
    assert layout.columns[1] == ["header", "toggles", "weather"]


def test_switching_on_a_drawer_module_goes_to_the_drawer():
    layout = base()
    layout.set_visible("clipboard", True, area="drawer")
    assert layout.drawer == ["timer", "notes", "clipboard"]


def test_move_between_and_within_columns():
    layout = base()
    assert layout.move("media", ("col", 1), 0)
    assert layout.columns == [["display", "audio"], ["media", "header", "toggles"]]
    # dropping after itself in the same list accounts for its own removal
    assert layout.move("display", ("col", 0), 2)
    assert layout.columns[0] == ["audio", "display"]


def test_area_rules():
    layout = base()
    assert not layout.move("timer", ("col", 0), 0, area="drawer")
    assert not layout.move("media", "drawer", 0, area="main")
    assert allowed("drawer", "pool") and allowed("main", "top")
    assert layout.where("timer") == "drawer"


def test_moving_to_the_pool_forgets_hidden():
    layout = base()
    layout.set_visible("media", False)
    layout.move("media", "pool")
    assert layout.where("media") == "pool" and "media" not in layout.to_config()["hidden"]


def test_remove_column_keeps_cards():
    layout = base()
    layout.remove_column(1)
    assert layout.columns == [["display", "audio", "media", "header", "toggles"]]
    layout.remove_column(0)  # the last column stays
    assert len(layout.columns) == 1


def test_empty_columns_are_dropped_on_save():
    layout = base()
    layout.add_column()
    assert len(layout.to_config()["columns"]) == 2


def test_tools_live_only_among_tools():
    layout = base()
    assert allowed("tool", "tools") and not allowed("tool", ("col", 0)) and not allowed("tool", "drawer")
    assert not allowed("main", "tools") and not allowed("drawer", "tools")
    layout.set_visible("capture", True, area="tool")
    assert layout.where("capture") == "tools" and layout.visible("capture")
    assert not layout.move("capture", ("col", 0), area="tool")
    layout.set_visible("capture", False, area="tool")
    assert layout.to_config()["hidden"] == ["capture"] and layout.to_config()["tools"] == ["capture"]


def test_presets_place_every_card_once():
    from cornerwindow.modules.layout import PRESETS, preset

    for name in PRESETS:
        placed = preset(name).placed()
        assert len(placed) == len(set(placed)), name


def test_new_card_goes_to_the_column_with_most_room():
    layout = base()  # column 1 has 3 cards, column 2 has 2
    layout.set_visible("weather", True)
    assert layout.where("weather") == ("col", 1)  # by count without measurements
    layout = base()
    layout.set_visible("weather", True, heights=[600, 900])  # column 2 has fewer but taller cards
    assert layout.where("weather") == ("col", 0)
    layout = base()
    layout.set_visible("weather", True, heights=[600])  # measurements don't match: by count
    assert layout.where("weather") == ("col", 1)


def test_overflowing_columns():
    from cornerwindow.modules.layout import overflowing

    assert overflowing([(900, 1000), (1200, 1000), (1300, None)]) == [1]
    assert overflowing([]) == []
