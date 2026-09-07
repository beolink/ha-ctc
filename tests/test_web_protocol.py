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
