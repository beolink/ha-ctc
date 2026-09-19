"""The sidebar page's layout (dashboard_views.py), checked without Home Assistant."""

import json
import re

import pytest

from conftest import COMPONENT

NEW_HA = (2026, 9)
OLD_HA = (2024, 12)
HIDDEN = {"condition": "state", "state_not": ["unavailable", "unknown"]}


def _headings(sections):
    return [s["cards"][0]["heading"] for s in sections]


def _section(sections, heading):
    return next(s for s in sections if s["cards"][0]["heading"] == heading)


TILE = "custom:ctc-ecozenith-tile"
ROWS = "custom:ctc-ecozenith-rows"


def _items(section):
    """(kind, tile card or row, entity_id) in reading order."""
    found = []
    for card in section["cards"][1:]:
        if card["type"] == TILE:
            found.append(("tile", card, card["tile"]["entity"]))
        elif card["type"] == ROWS:
            for row in card["rows"]:
                found.append(("row", row, row["entity"]))
    return found


def _keys(pump, section, kind=None):
    by_entity = {entity_id: key for key, entity_id in pump["entities"].items()}
    return [by_entity[e] for k, _c, e in _items(section) if kind in (None, k)]


def _overview(dashboard_views, pump, version=NEW_HA, lang="sv"):
    sections, _ = dashboard_views.overview_sections(pump, dashboard_views.TEXT[lang], version)
    return sections


def test_the_overview_has_its_sections_in_order(dashboard_views, pumps):
    assert _headings(_overview(dashboard_views, pumps["vsh"])) == [
        "CTC EcoZenith i255",
        "Temperaturer",
        "Varmvatten",
        "Energi och värmefaktor",
        "Styrning",
        "Kompressor och köldkrets",
        "Pumpens inställningar",
        "Om enheten",
    ]


def test_status_stays_on_the_page_whatever_its_state(dashboard_views, pumps):
    pump = pumps["vsh"]
    status = _overview(dashboard_views, pump)[0]
    assert _keys(pump, status, "tile") == ["system_status", "hp1_status"]
    assert _keys(pump, status, "row") == [
        "compressor_running", "defrosting", "immersion_active", "alarm", "blocked",
        "smartgrid_active", "hs1_status", "sg_mode",
    ]
    assert "visibility" not in status
    assert all("visibility" not in card for card in status["cards"])
    assert all("hide_unavailable" not in row for kind, row, _e in _items(status) if kind == "row")


def test_readings_hide_while_they_have_nothing_to_show(dashboard_views, pumps):
    pump = pumps["vsh"]
    temperatures = _section(_overview(dashboard_views, pump), "Temperaturer")
    for kind, item, entity_id in _items(temperatures):
        if kind == "tile":
            assert item["visibility"] == [{**HIDDEN, "entity": entity_id}]
        else:
            assert item["hide_unavailable"] is True
    rows_card = next(c for c in temperatures["cards"] if c["type"] == ROWS)
    (either,) = rows_card["visibility"]
    assert either["condition"] == "or"
    # The whole section goes when every value in it has.
    (section_either,) = temperatures["visibility"]
    assert len(section_either["conditions"]) == len(_items(temperatures))


def test_the_values_that_matter_are_whole_width_tiles_and_the_rest_rows(dashboard_views, pumps):
    pump = pumps["vsh"]
    sections = _overview(dashboard_views, pump)
    tiles = [c for s in sections for c in s["cards"] if c["type"] == TILE]
    assert all(c["grid_options"] == {"columns": 12} for c in tiles)
    temperatures = _section(sections, "Temperaturer")
    assert _keys(pump, temperatures, "tile") == ["outdoor_temp", "room_temp_1"]
    assert _keys(pump, temperatures, "row")[:3] == ["set_room_1", "hs1_flow", "hs1_flow_setpoint"]
    rows = {row["name"] for kind, row, _e in _items(temperatures) if kind == "row"}
    assert "Framledning börvärde VS1" in rows


def test_the_energy_section_picks_the_display_rows_out_by_label(dashboard_views, pumps):
    pump = pumps["vsh"]
    energy = _section(_overview(dashboard_views, pump), "Energi och värmefaktor")
    tiles = _keys(pump, energy, "tile")
    assert tiles == [
        "cop_day", "cop_year", "cop_first_year", "cop_lifetime",
        "p22_avgiven_varme", "p22_tillford_effekt",
    ]
    rows = _keys(pump, energy, "row")
    assert rows[:2] == ["p30_avgiven_varme_totalt", "compressor_kwh"]
    # Modbus 62341 counts the same consumed energy, and fresher.
    assert "p30_tillford_energi_totalt" not in rows
    # Periods are no lifetime counters.
    assert not [k for k in tiles + rows if "30_dagar" in k]


def test_display_roles_in_english_and_without_modbus(dashboard_views):
    pump = {
        "entities": {"p22_energy_output": "sensor.a", "p22_energy_add": "sensor.b"},
        "pages": [{"title": "Heat pump", "values": [
            {"key": "p22_energy_output", "label": "Energy output", "unit": "kW"},
            {"key": "p22_energy_add", "label": "Energy add", "unit": "kW"},
            {"key": "p22_energy_output_24", "label": "Energy output/24h", "unit": "kW"},
        ]}],
        "energy_out": "p30_out",
        "energy_in": "p30_in",
    }
    assert dashboard_views.display_roles(pump) == {
        dashboard_views.HEAT_POWER: "p22_energy_output",
        dashboard_views.SUPPLIED_POWER: "p22_energy_add",
        dashboard_views.ENERGY_OUT: "p30_out",
        dashboard_views.ENERGY_IN: "p30_in",
    }


def test_controls_get_their_own_features_beside_the_name(dashboard_views, pumps):
    pump = pumps["vsh"]
    pump["entities"]["release_control"] = "button.ctc_ecozenith_i255_slapp_all_styrning"
    pump["names"]["release_control"] = "Släpp all styrning"
    control = _section(_overview(dashboard_views, pump), "Styrning")
    by_key = dict(zip(_keys(pump, control), (c["tile"] for _k, c, _e in _items(control))))
    assert list(by_key) == [
        "ctl_room_setpoint_1", "ctl_zone_mode_1", "ctl_dhw_mode", "ctl_dhw_setpoint",
        "ctl_extra_dhw", "ctl_price_mode", "ctl_max_rps", "ctl_immersion_lower",
        "ctl_immersion_upper", "release_control",
    ]
    room = by_key["ctl_room_setpoint_1"]
    # "Styrning" is the heading, so the tile drops the "Styr" every name starts with.
    assert room["name"] == "Rumsbörvärde"
    assert by_key["ctl_immersion_lower"]["name"] == "Max elpatron nedre"
    assert by_key["release_control"]["name"] == "Släpp all styrning"
    assert room["features"] == [{"type": "numeric-input", "style": "buttons"}]
    assert room["features_position"] == "inline"
    assert by_key["ctl_max_rps"]["features"] == [{"type": "numeric-input", "style": "slider"}]
    # A slider needs the width, so it goes under the name.
    assert "features_position" not in by_key["ctl_max_rps"]
    assert by_key["ctl_dhw_mode"]["features"] == [{"type": "select-options"}]
    assert by_key["ctl_dhw_mode"]["features_position"] == "inline"
    release = by_key["release_control"]
    press = {"action": "perform-action", "perform_action": "button.press",
             "target": {"entity_id": release["entity"]}}
    assert release["tap_action"] == press and release["icon_tap_action"] == press
    assert release["hide_state"] is True and "features" not in release
    # Tapping a control's name explains it; the button keeps its press.
    assert room["tap_action"] == {"action": "none"}
    assert room["icon_tap_action"] == {"action": "more-info"}
    # A control is never hidden: an unavailable one says something is wrong.
    assert all("visibility" not in c for _k, c, _e in _items(control))


def test_without_control_the_section_says_how_to_turn_it_on(dashboard_views, pumps):
    control = _section(_overview(dashboard_views, pumps["pt"]), "Styrning")
    (note,) = control["cards"][1:]
    assert note["type"] == "markdown"
    assert "Tillåt styrning av värmepumpen" in note["content"]
    assert "/config/integrations/integration/ctc_ecozenith" in note["content"]


def test_control_switched_on_but_every_control_disabled_leaves_no_section(dashboard_views, pumps):
    pump = pumps["vsh"]
    for key in [k for k in pump["entities"] if k.startswith("ctl_")]:
        del pump["entities"][key]
    assert "Styrning" not in _headings(_overview(dashboard_views, pump))


def test_trend_graphs_only_where_the_frontend_has_them(dashboard_views, pumps):
    pump = pumps["vsh"]

    def features(version):
        return {
            c["tile"]["entity"]: c["tile"].get("features")
            for s in _overview(dashboard_views, pump, version)
            for c in s["cards"] if c["type"] == TILE and c["tile"].get("features")
        }

    new = features(NEW_HA)
    trend = [{"type": "trend-graph", "hours_to_show": 24}]
    for key in ("outdoor_temp", "room_temp_1", "dhw_temp", "hp1_rps"):
        assert new[pump["entities"][key]] == trend
    for version in (OLD_HA, (2025, 8)):
        assert pump["entities"]["outdoor_temp"] not in features(version)


def test_the_display_view_has_a_section_per_page_with_the_panels_row_names(dashboard_views, pumps):
    pump = pumps["pt"]
    sections = dashboard_views.display_sections(pump, dashboard_views.TEXT["sv"], NEW_HA)
    assert _headings(sections) == [
        "CTC EcoZenith i550 Pro", "Varmvatten", "Extern bufferttank",
        "Historisk driftinfo", "Styrenhet",
    ]
    history = _section(sections, "Historisk driftinfo")
    names = [row["name"] for _k, row, _e in _items(history)]
    assert "Avgiven värme totalt" in names
    assert not [n for n in names if n.startswith("Historisk driftinfo")]
    # Disabled rows are not on the page, and the heating system page had nothing else.
    assert "Värmesystem" not in _headings(sections)
    assert all(row["hide_unavailable"] for _k, row, _e in _items(history))


def test_a_name_the_user_gave_a_display_value_is_kept(dashboard_views, pumps):
    pump = pumps["vsh"]
    pump["names"]["p30_avgiven_varme_totalt"] = "Värme ut sedan start"
    sections = dashboard_views.display_sections(pump, dashboard_views.TEXT["sv"], NEW_HA)
    names = [row["name"] for s in sections for _k, row, _e in _items(s)]
    assert "Värme ut sedan start" in names


def test_entities_nobody_placed_end_up_under_other_and_are_still_explained(dashboard_views, pumps):
    pump = pumps["vsh"]
    pump["entities"]["something_new"] = "sensor.ctc_ecozenith_i255_something_new"
    pump["names"]["something_new"] = "Något nytt"
    sections, placed = dashboard_views.overview_sections(pump, dashboard_views.TEXT["sv"], NEW_HA)
    other = _section(sections, "Övrigt")
    # Display values have a view of their own and do not spill into it.
    assert _keys(pump, other) == ["something_new"]
    assert "something_new" in placed
    (_kind, row, _entity), = _items(other)
    assert row["explanation"] == dashboard_views.UNKNOWN_EXPLANATION
    assert row["source"] == dashboard_views.UNKNOWN_SOURCE


def test_values_of_a_page_no_longer_harvested_are_left_off(dashboard_views, pumps):
    # The i550 Pro once had four more pages ticked. Their entities stay in the
    # registry, unavailable for good, and belong nowhere on the page.
    pump = pumps["pt"]
    orphans = {k: e for k, e in pump["entities"].items() if k.startswith(("p23_", "p24_", "p27_", "p58_"))}
    assert orphans
    pump["pages"] = [page for page in pump["pages"] if page["title"] == "Historisk driftinfo"]
    config = dashboard_views.build_dashboard([pump], "sv", NEW_HA)
    on_page = dashboard_views.entity_ids(config)
    assert not set(orphans.values()) & on_page
    assert "Övrigt" not in _headings(config["views"][0]["sections"])
    assert [v["title"] for v in config["views"]] == ["Översikt", "Displayvärden"]


def test_every_enabled_entity_is_somewhere_on_the_page_and_only_once(dashboard_views, pumps):
    for pump in pumps.values():
        config = dashboard_views.build_dashboard([pump], "en", NEW_HA)
        views = {v["path"]: v for v in config["views"]}
        overview = [e for s in views["overview"]["sections"] for _k, _c, e in _items(s)]
        display = [e for s in views["display"]["sections"] for _k, _c, e in _items(s)]
        assert len(overview) == len(set(overview))
        assert len(display) == len(set(display))
        assert set(overview) | set(display) == set(pump["entities"].values())


def test_one_pump_gets_plain_tab_names(dashboard_views, pumps):
    config = dashboard_views.build_dashboard([pumps["vsh"]], "en", NEW_HA)
    assert config["title"] == "CTC EcoZenith"
    assert [(v["title"], v["path"], v["type"]) for v in config["views"]] == [
        ("Översikt", "overview", "sections"),
        ("Displayvärden", "display", "sections"),
    ]


def test_several_pumps_get_a_tab_each_sorted_by_name(dashboard_views, pumps):
    config = dashboard_views.build_dashboard([pumps["vsh"], pumps["pt"], None], "sv", NEW_HA)
    assert [(v["title"], v["path"]) for v in config["views"]] == [
        ("CTC EcoZenith i255", "overview-1"),
        ("CTC EcoZenith i255: displayvärden", "display-1"),
        ("CTC EcoZenith i550 Pro", "overview-2"),
        ("CTC EcoZenith i550 Pro: displayvärden", "display-2"),
    ]


def test_a_pump_without_display_pages_has_no_display_tab(dashboard_views, pumps):
    pump = pumps["pt"]
    pump["pages"] = []
    pump["energy_out"] = pump["energy_in"] = None
    config = dashboard_views.build_dashboard([pump], "sv", NEW_HA)
    assert [v["path"] for v in config["views"]] == ["overview"]


def test_headings_follow_the_panel_language(dashboard_views, pumps):
    pump = pumps["vsh"]
    pump["language"] = "en"
    config = dashboard_views.build_dashboard([pump], "sv", NEW_HA)
    assert [v["title"] for v in config["views"]] == ["Overview", "Display values"]
    headings = _headings(config["views"][0]["sections"])
    assert "Temperatures" in headings and "Energy and COP" in headings


def test_no_running_pump_gives_a_note(dashboard_views):
    config = dashboard_views.build_dashboard([], "sv-SE", NEW_HA)
    (view,) = config["views"]
    (section,) = view["sections"]
    heading, note = section["cards"]
    assert heading["heading"] == "CTC EcoZenith"
    assert note["type"] == "markdown" and note["content"].startswith("Ingen CTC-värmepump")
    assert dashboard_views.build_dashboard([], "de", NEW_HA)["views"][0]["title"] == "Overview"


def test_the_page_is_plain_json_and_its_entities_can_be_listed(dashboard_views, pumps):
    config = dashboard_views.build_dashboard(list(pumps.values()), "sv", NEW_HA)
    assert json.loads(json.dumps(config)) == config
    assert dashboard_views.entity_ids(config) == set(pumps["vsh"]["entities"].values()) | set(
        pumps["pt"]["entities"].values()
    )


def _source(name):
    return (COMPONENT / name).read_text(encoding="utf-8")


def _layout_keys(dashboard_views):
    tiles, rows = dashboard_views._OPERATION
    used = set(tiles) | set(rows) | set(dashboard_views._CONTROL)
    for _id, _icon, tiles, rows in dashboard_views._READINGS + dashboard_views._TECHNICAL:
        used |= set(tiles) | set(rows)
    return used


def test_every_key_on_the_page_is_a_unique_id_the_integration_creates(dashboard_views, const):
    created = {d.key for d in const.MODBUS_SENSORS + const.MODBUS_SETTINGS}
    created |= {r.key for r in const.CONTROL_NUMBERS + const.CONTROL_SELECTS}
    created |= set(re.findall(r'DerivedBinary\(\s*"([a-z0-9_]+)"', _source("binary_sensor.py")))
    identity = re.search(r"# What the unit is.*?\):\n", _source("sensor.py"), re.S)
    created |= set(re.findall(r'\("([a-z_]+)", "', identity.group(0)))
    spans = re.search(r"for span in \(([^)]*)\)", _source("sensor.py")).group(1)
    created |= {f"cop_{span}" for span in re.findall(r'"([a-z_]+)"', spans)}
    created |= set(re.findall(r'_\{host\}_([a-z_]+)"', _source("button.py")))

    used = {key for key in _layout_keys(dashboard_views) if not key.startswith("@")}
    assert used - created == set()
    # And the other way: nothing the integration makes is left to "Other".
    assert created - used == set()


@pytest.mark.parametrize("key", sorted(["outdoor_temp", "room_temp_1", "dhw_temp", "hp1_rps"]))
def test_trend_keys_are_tiles(dashboard_views, key):
    assert key in dashboard_views._TREND_KEYS
    tiles = {k for _i, _c, t, _r in dashboard_views._READINGS + dashboard_views._TECHNICAL for k in t}
    assert key in tiles


def test_every_control_name_starts_with_the_prefix_the_page_drops(dashboard_views, const):
    for register in const.CONTROL_NUMBERS + const.CONTROL_SELECTS:
        assert register.name.startswith(dashboard_views._CONTROL_PREFIX), register.key


def test_no_reading_without_a_device_class_is_left_with_the_default_icon(const):
    for description in const.MODBUS_SENSORS + const.MODBUS_SETTINGS:
        if description.device_class is None:
            assert description.icon, f"{description.key} would show Home Assistant's eye"


def _every_item(dashboard_views, pump, lang="sv"):
    config = dashboard_views.build_dashboard([pump], lang, NEW_HA)
    return [
        (card, item, entity_id)
        for view in config["views"] for section in view["sections"]
        for card in section["cards"][1:] if card["type"] in (TILE, ROWS)
        for _kind, item, entity_id in _items({"cards": [None, card]})
    ]


def test_every_value_on_both_pages_is_explained_and_says_where_it_comes_from(dashboard_views, pumps):
    for pump in pumps.values():
        pump["display_interval"] = 1800
        for card, item, entity_id in _every_item(dashboard_views, pump):
            assert item.get("explanation"), entity_id
            assert item.get("source"), entity_id
            assert card["more_info"] == "Mer info"


def test_sources_name_the_register_or_the_display_page(dashboard_views, pumps):
    pump = pumps["vsh"]
    pump["display_interval"] = 1800
    by_entity = {e: item for _c, item, e in _every_item(dashboard_views, pump)}
    assert by_entity[pump["entities"]["outdoor_temp"]]["source"] == "Modbus-register 62000"
    assert by_entity[pump["entities"]["set_room_1"]]["source"] == (
        "Modbus-register 61509, lagrad inställning som bara läses"
    )
    assert by_entity[pump["entities"]["ctl_dhw_mode"]]["source"] == "Modbus-register 1007, flyktigt"
    assert by_entity[pump["entities"]["p22_avgiven_varme"]]["source"] == (
        "Displaysidan Driftinfo Värmepump, hämtas var 30:e minut"
    )
    assert by_entity[pump["entities"]["compressor_running"]]["source"].endswith("62017")


def test_display_rows_are_explained_by_their_label(explanations):
    assert explanations.display_explanation("VP in/ut 2", "Driftinfo Värmepump").startswith(
        "Vattnets temperatur in till och ut"
    )
    # The longest match wins.
    assert "invertern driver kompressormotorn med just nu" in explanations.display_explanation(
        "Inverter Motoreffekt", "Driftinfo Värmepump"
    )
    assert explanations.display_explanation("Inverter", "x").startswith("Temperaturen i invertern")
    assert explanations.display_explanation("Avgiven värme totalt", "x").startswith("All värme")
    assert explanations.display_explanation("Avgiven värme/30 dagar", "x").startswith("Värme levererad")
    assert explanations.display_explanation("Avgiven värme", "x").startswith("Värmeeffekten")
    assert explanations.display_explanation("Energy output total", "x").startswith("All värme")
    # The i550 Pro's overview picture has numbers without a heading of their own.
    unnamed = explanations.display_explanation("CTC EcoZenith i550 Pro 3", "CTC EcoZenith i550 Pro")
    assert unnamed == explanations.display_explanation("Värde 4", "CTC EcoZenith i550 Pro")
    assert "utan en egen rubrik" in unnamed
    assert explanations.display_explanation("Något helt okänt", "x") == explanations._GENERIC


def test_every_key_the_integration_makes_has_an_explanation(explanations, const, dashboard_views):
    used = {key for key in _layout_keys(dashboard_views) if not key.startswith("@")}
    missing = sorted(key for key in used if not explanations.explain(key))
    assert missing == []
    assert sorted(key for key in used if not explanations.source(key)) == []


def test_explanations_use_no_dashes_as_punctuation(explanations):
    texts = [
        *explanations.MODBUS.values(), *explanations.CONTROL.values(),
        *explanations.DERIVED.values(), *explanations.IDENTITY.values(),
        *(text for _prefix, text in explanations._DISPLAY),
        explanations._UNNAMED, explanations._GENERIC,
    ]
    for text in texts:
        assert "—" not in text and " – " not in text and " - " not in text, text


def test_the_button_keeps_its_press_and_is_explained_on_hover(dashboard_views, pumps):
    pump = pumps["vsh"]
    pump["entities"]["release_control"] = "button.ctc_ecozenith_i255_slapp_all_styrning"
    pump["names"]["release_control"] = "Släpp all styrning"
    control = _section(_overview(dashboard_views, pump), "Styrning")
    card = next(c for _k, c, e in _items(control) if e.startswith("button."))
    assert card["tile"]["tap_action"]["action"] == "perform-action"
    assert card["explanation"].startswith("Släpper all styrning")


def test_the_web_interface_is_a_tab_of_its_own_only_when_asked_for(dashboard_views, pumps, const):
    pump = pumps["vsh"]
    assert [v["path"] for v in dashboard_views.build_dashboard([pump], "sv", NEW_HA)["views"]] == [
        "overview", "display",
    ]
    pump["web_url"] = const.web_interface_url("10.0.40.55")
    views = dashboard_views.build_dashboard([pump], "sv", NEW_HA)["views"]
    # Last of all, so it is the rightmost tab.
    assert [v["path"] for v in views] == ["overview", "display", "web"]
    web = views[-1]
    assert web["title"] == "Webbgränssnitt" and web["type"] == "panel"
    assert web["cards"] == [{"type": "iframe", "url": "http://10.0.40.55/main.html"}]
    # No entity of the integration's own is on it.
    assert dashboard_views.entity_ids({"views": [web]}) == set()


def test_the_web_tab_comes_after_every_pumps_pages_and_carries_its_name(dashboard_views, pumps, const):
    for pump in pumps.values():
        pump["web_url"] = const.web_interface_url("10.0.0.1", 8080)
    views = dashboard_views.build_dashboard(list(pumps.values()), "sv", NEW_HA)["views"]
    assert [v["path"] for v in views] == [
        "overview-1", "display-1", "overview-2", "display-2", "web-1", "web-2",
    ]
    assert views[-2]["title"] == "CTC EcoZenith i255: webbgränssnitt"
    assert views[-1]["cards"][0]["url"] == "http://10.0.0.1:8080/main.html"


def test_the_address_of_the_web_interface(const):
    # The display answers the same page for any main.* address, and the port is
    # only written out when it is not the usual one.
    assert const.web_interface_url("10.0.40.55") == "http://10.0.40.55/main.html"
    assert const.web_interface_url("10.0.40.55", 80) == "http://10.0.40.55/main.html"
    assert const.web_interface_url("10.0.40.55", 8080) == "http://10.0.40.55:8080/main.html"
