"""What an installation has ever reported (seen.py), and what the page leaves out."""

import asyncio

from test_dashboard import NEW_HA, _headings, _items, _section, _values_card, _view


class FakeStore:
    def __init__(self, stored=None):
        self.stored = stored
        self.saves = []

    async def async_load(self):
        return self.stored

    def async_delay_save(self, data_func, delay):
        self.saves.append((data_func(), delay))


def test_only_a_number_that_is_exactly_zero_counts_as_zero(seen):
    assert seen.is_zero(0) and seen.is_zero(0.0) and seen.is_zero("0.0") and seen.is_zero("0")
    assert not seen.is_zero(0.1) and not seen.is_zero("-0.5") and not seen.is_zero(False)
    assert not seen.is_zero("Normal") and not seen.is_zero("unavailable") and not seen.is_zero(None)


def test_values_other_than_zero_are_remembered_and_announced_once(seen):
    store, announced = FakeStore(), []
    tracker = seen.SeenValues(store, on_new=lambda: announced.append(True))
    tracker.note({"hp1_fan": 0.0, "hp1_rps": 42.5, "system_status": "Värme", "room_temp_2": None})
    assert tracker.keys == {"hp1_rps", "system_status"}
    assert announced == [True]
    assert store.saves[-1] == ({"keys": ["hp1_rps", "system_status"]}, seen.SAVE_DELAY_SECONDS)
    # Nothing new: no save, no announcement. A value going back to zero is still known.
    tracker.note({"hp1_fan": 0.0, "hp1_rps": 0.0})
    assert announced == [True] and len(store.saves) == 1
    assert tracker.unused({"hp1_fan": "0.0", "hp1_rps": "0.0", "outdoor_temp": "17.0"}) == {"hp1_fan"}


def test_what_was_seen_survives_a_restart(seen):
    tracker = seen.SeenValues(FakeStore({"keys": ["hp1_fan"]}))
    asyncio.run(tracker.async_load())
    assert tracker.unused({"hp1_fan": "0.0", "hp1_brine_pump": "0.0"}) == {"hp1_brine_pump"}
    broken = seen.SeenValues(FakeStore("not a dict"))
    asyncio.run(broken.async_load())
    assert broken.keys == set()


def _keys_on_page(pump, config, paths=("overview", "controls", "performance")):
    """The keys on the tabs that pick what to show, which the full list does not."""
    by_entity = {entity_id: key for key, entity_id in pump["entities"].items()}
    return {
        by_entity[e]
        for view in config["views"] if view["path"] in paths
        for section in view["sections"]
        for _k, _c, e in _items(section) if e in by_entity
    }


def test_readings_only_ever_zero_are_left_out_but_status_control_and_settings_stay(
    dashboard_views, pumps
):
    pump = pumps["vsh"]
    pump["unused"] = ["hp1_brine_pump", "current_l1", "current_l2", "current_l3", "dhw_capacity",
                      "p30_emxxx", "ctl_max_rps", "set_heating_mode_1", "system_status"]
    config = dashboard_views.build_dashboard([pump], "sv", NEW_HA)
    on_page = _keys_on_page(pump, config)
    for key in ("hp1_brine_pump", "current_l1", "dhw_capacity", "p30_emxxx"):
        assert key not in on_page, key
    # What is shown whatever it reads.
    for key in ("ctl_max_rps", "set_heating_mode_1", "system_status"):
        assert key in on_page, key
    # And a reading left out does not reappear under "Övrigt".
    assert "Övrigt" not in _headings(_view(config, "performance")["sections"])
    # The tab with every value is the one place it is still to be found.
    listed = {row["entity"] for row in _values_card(config)["rows"] if "entity" in row}
    assert pump["entities"]["hp1_brine_pump"] in listed


def test_a_section_the_installation_has_nothing_for_goes(dashboard_views, pumps):
    # The i550 Pro's compressor has never run: its refrigerant circuit is all zeros.
    pump = pumps["pt"]
    compressor = [k for _i, _c, tiles, rows in dashboard_views._TECHNICAL[:1] for k in (*tiles, *rows)]
    pump["unused"] = [k for k in compressor if k in pump["entities"]]
    config = dashboard_views.build_dashboard([pump], "sv", NEW_HA)
    headings = _headings(_view(config, "performance")["sections"])
    assert "Kompressor och köldkrets" not in headings
    assert "Kompressorn det senaste dygnet" not in headings
    assert "Temperaturer" in headings
    # The full list still has them, under the heading that names the circuit.
    assert "Kompressor och köldkrets" in [
        row["heading"] for row in _values_card(config)["rows"] if "heading" in row
    ]


def test_a_display_row_only_ever_zero_is_left_out_where_the_page_picks(dashboard_views, pumps):
    pump = pumps["vsh"]
    pump["unused"] = ["p22_avgiven_varme", "p30_avgiven_kyla_totalt"]
    config = dashboard_views.build_dashboard([pump], "sv", NEW_HA)
    on_page = _keys_on_page(pump, config)
    assert "p22_avgiven_varme" not in on_page
    assert "p22_tillford_effekt" in on_page
    # In the full list it is there, under the page the panel prints it on.
    names = [row["name"] for row in _values_card(config)["rows"] if "entity" in row]
    assert "Avgiven värme" in names and "Avgiven kyla totalt" in names
