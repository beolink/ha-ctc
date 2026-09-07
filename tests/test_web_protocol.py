"""Tests for the display protocol decoding.

The fixtures are real responses captured from a CTC EcoZenith i255 driving an
EcoAir 720M.
"""

from __future__ import annotations


def test_screen_map_pairs_pages_with_screens(web_api, sm_all):
    pages = web_api.parse_screen_map(sm_all)
    # The unit under test reports 281 pages.
    assert len(pages) > 200
    # Page 22 is the heat pump's operation data page.
    assert 118 in pages[22]
    # Every page lists at least one screen.
    assert all(screens for screens in pages.values() if screens is not None)


def test_vars_keep_strings_and_ints_apart(web_api):
    parsed = web_api.parse_vars('17|1|245|""|0|')
    assert parsed == [17, 1, 245, "", 0]


def test_vars_ignores_trailing_separator(web_api):
    assert web_api.parse_vars("1|2|3|") == [1, 2, 3]


def test_group_selection_counts_alternatives_not_slots(web_api):
    # Codes 0, 1 and 2 occupy three slots, 3 and 4 occupy two. A selector of 2
    # must land on the third alternative, not the third slot.
    group = [3, 100, 3, 101, 1, 5, 7, 3, 102]
    assert web_api._select_from_group(group, 0) == ("text", 100)
    assert web_api._select_from_group(group, 1) == ("text", 101)
    assert web_api._select_from_group(group, 2) == ("fmt", 7)
    assert web_api._select_from_group(group, 3) == ("text", 102)


def test_group_selection_returns_none_past_the_end(web_api):
    assert web_api._select_from_group([3, 100], 9) is None
    assert web_api._select_from_group("not a list", 0) is None


def test_primary_variant_takes_the_normal_rendering(web_api):
    # CTC gives a normal rendering first and "no sensor fitted" variants after.
    spec = ["%.1f°C", 0, [1, 15], "--.-°C", 9999, [None]]
    fmt, indices = web_api._primary_variant(spec)
    assert fmt == "%.1f°C"
    assert indices == [15]


def test_primary_variant_rejects_a_non_format_entry(web_api):
    assert web_api._primary_variant([0, 1, 2]) == (None, [])


def test_balanced_array_survives_nesting_and_strings(web_api, wp118):
    raw = web_api._balanced_array(wp118, "p118t2")
    assert raw is not None
    assert raw.startswith("[") and raw.endswith("]")
    # Nested arrays must be included, not cut at the first closing bracket.
    assert raw.count("[") == raw.count("]")


def test_tap_target_prefers_the_icon_above_a_caption(web_api):
    # Home screen tiles are an icon with the caption drawn underneath as its own
    # element. Tapping the caption's centre can miss the touch area.
    widget = web_api.Widget
    icon = widget(index=0, kind=1, x=338, y=65, width=79, height=79, visible=True)
    caption = widget(
        index=1, kind=3, x=322, y=150, width=111, height=22, visible=True, label="Driftinfo"
    )
    assert web_api.tap_target([icon, caption], caption) == icon.centre


def test_tap_target_falls_back_to_the_caption(web_api):
    widget = web_api.Widget
    caption = widget(
        index=0, kind=3, x=10, y=10, width=100, height=20, visible=True, label="Ensam"
    )
    assert web_api.tap_target([caption], caption) == caption.centre


def test_tap_target_ignores_an_icon_below_the_caption(web_api):
    widget = web_api.Widget
    caption = widget(index=0, kind=3, x=10, y=10, width=100, height=20, visible=True, label="X")
    below = widget(index=1, kind=1, x=10, y=60, width=80, height=80, visible=True)
    assert web_api.tap_target([below, caption], caption) == caption.centre


def test_widgets_overlap_horizontally(web_api):
    widget = web_api.Widget
    left = widget(index=0, kind=1, x=0, y=0, width=50, height=10, visible=True)
    over = widget(index=1, kind=1, x=40, y=0, width=50, height=10, visible=True)
    clear = widget(index=2, kind=1, x=60, y=0, width=50, height=10, visible=True)
    assert left.overlaps_horizontally(over)
    assert not left.overlaps_horizontally(clear)
