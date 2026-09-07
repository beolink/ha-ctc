"""Tests for register decoding, block planning and the value catalogue."""

from __future__ import annotations

import pytest


# ------------------------------------------------------------------- Modbus


def test_signed_decoding_handles_negative_temperatures(modbus_api):
    assert modbus_api.decode_signed(100) == 100
    assert modbus_api.decode_signed(65436) == -100
    assert modbus_api.decode_signed(32767) == 32767
    assert modbus_api.decode_signed(32768) == -32768


def test_pair_decoding_is_least_significant_word_first(modbus_api):
    # CTC sends 32 bit counters low word first.
    assert modbus_api.decode_pair(0x0001, 0x0002) == 0x00020001


def test_sentinels_are_recognised(modbus_api):
    for missing in (9999, -9999, 10000, -10000, 32767):
        assert modbus_api.is_sentinel(missing)
    assert not modbus_api.is_sentinel(215)


def test_block_planner_groups_neighbours(modbus_api, const):
    sensor = const.ModbusSensor
    plan = modbus_api.plan_blocks(
        (
            sensor("a", 62000, "a"),
            sensor("b", 62003, "b"),
            sensor("c", 62005, "c"),
            sensor("far", 62300, "far"),
        )
    )
    assert plan == [(62000, 6), (62300, 1)]


def test_block_planner_respects_the_hundred_register_limit(modbus_api, const):
    sensor = const.ModbusSensor
    sensors = tuple(
        sensor(f"s{offset}", 62000 + offset, "s") for offset in range(0, 240, 8)
    )
    plan = modbus_api.plan_blocks(sensors)
    assert plan, "expected at least one block"
    assert all(count <= modbus_api.MAX_BLOCK for _, count in plan)


def test_block_planner_covers_every_configured_register(modbus_api, const):
    descriptions = const.MODBUS_SENSORS + const.MODBUS_SETTINGS
    plan = modbus_api.plan_blocks(descriptions)
    covered = {
        address
        for start, count in plan
        for address in range(start, start + count)
    }
    for description in descriptions:
        for offset in range(description.count):
            assert description.address + offset in covered


def test_block_planner_is_empty_for_no_sensors(modbus_api):
    assert modbus_api.plan_blocks(()) == []


# ---------------------------------------------------------------- catalogue


def test_scale_follows_the_display_format(catalogue):
    assert catalogue._decimals("%.1f°C") == pytest.approx(0.1)
    assert catalogue._decimals("%.-1f%%") == pytest.approx(0.1)
    assert catalogue._decimals("%d") == pytest.approx(1.0)


def test_unit_ignores_the_conversion_itself(catalogue):
    # "%" opens every conversion, so a naive search returns a percent sign for
    # a temperature.
    assert catalogue._unit("%.1f°C") == "°C"
    assert catalogue._unit("%.-1f%%") == "%"
    assert catalogue._unit("%.-1frps") == "rps"


def test_unit_falls_back_to_the_row_name(catalogue):
    assert catalogue._unit("%.1f", "Avgiven värme (kW)") == "kW"
    assert catalogue._unit("%.1f", "Utetemperatur °C") == "°C"
    assert catalogue._unit("%d", "Timer avfrostning") is None


def test_label_cleaning_drops_a_trailing_unit(catalogue):
    assert catalogue._clean_label("Avgiven värme (kW)") == "Avgiven värme"
    assert catalogue._clean_label("Utetemperatur °C") == "Utetemperatur"
    assert catalogue._clean_label("Kompressor") == "Kompressor"


def test_separators_are_not_readings(catalogue):
    assert catalogue.has_conversion("%.1f")
    assert not catalogue.has_conversion(" / ")
    assert not catalogue.has_conversion(" , ")


def test_numeric_value_filters_missing_sensors(catalogue, const):
    value = const.SlowValue(
        key="k", label="l", page=1, screen=2, fmt="%.1f", var_indices=[1], scale=0.1
    )
    assert catalogue.numeric_value(value, [0, 215]) == pytest.approx(21.5)
    assert catalogue.numeric_value(value, [0, 9999]) is None
    assert catalogue.numeric_value(value, [0]) is None
    assert catalogue.numeric_value(value, [0, "text"]) is None


def test_rows_break_on_the_left_hand_column(catalogue, web_api):
    widget = web_api.Widget
    widgets = [
        widget(index=0, kind=3, x=5, y=100, width=190, height=28, visible=True, label="Laddpump"),
        widget(index=1, kind=3, x=195, y=100, width=60, height=28, visible=True, label="Till"),
        widget(index=2, kind=3, x=255, y=100, width=40, height=28, visible=True, value_fmt="%.-1f%%", value_vars=[3]),
        widget(index=3, kind=3, x=5, y=128, width=190, height=28, visible=True, label="Brinepump"),
        widget(index=4, kind=3, x=195, y=128, width=60, height=28, visible=True, value_fmt="%.-1f%%", value_vars=[4]),
    ]
    pairing = catalogue._pair_labels(widgets)
    assert pairing[2] == "Laddpump"
    assert pairing[4] == "Brinepump"


def test_flow_layout_widgets_stay_on_their_row(catalogue, web_api):
    # A widget parked far to the left is laid out after the previous one, so it
    # belongs to the same row rather than starting a new one.
    widget = web_api.Widget
    widgets = [
        widget(index=0, kind=3, x=5, y=50, width=190, height=28, visible=True, label="VP in/ut"),
        widget(index=1, kind=3, x=195, y=50, width=50, height=28, visible=True, value_fmt="%.1f", value_vars=[1]),
        widget(index=2, kind=3, x=-20000, y=50, width=10, height=28, visible=True, value_fmt=" / ", value_vars=[]),
        widget(index=3, kind=3, x=-20000, y=50, width=40, height=28, visible=True, value_fmt="%.1f", value_vars=[2]),
    ]
    pairing = catalogue._pair_labels(widgets)
    assert pairing[1] == "VP in/ut 1"
    assert pairing[3] == "VP in/ut 2"


def test_storage_round_trip_keeps_the_catalogue(catalogue, const):
    page = const.SlowPage(page=22, title="Driftinfo Värmepump", screens=[1, 0, 118])
    page.values.append(
        const.SlowValue(
            key="p22_avgiven_varme",
            label="Avgiven värme",
            page=22,
            screen=118,
            fmt="%.1f",
            var_indices=[97],
            unit="kW",
            scale=0.1,
        )
    )
    restored = catalogue.pages_from_storage(catalogue.pages_to_storage([page]))
    assert len(restored) == 1
    assert restored[0].title == "Driftinfo Värmepump"
    assert restored[0].values[0].unit == "kW"
    assert restored[0].values[0].var_indices == [97]


def test_storage_discards_damaged_entries(catalogue):
    assert catalogue.pages_from_storage([{"nonsense": True}]) == []
    assert catalogue.pages_from_storage(None) == []


# ---------------------------------------------------------------- discovery


def test_model_names_come_from_the_settings_file(discovery):
    found = discovery.DiscoveredDisplay(host="192.168.1.55", settings_name="settings_ezi2xx.bin")
    assert "i255" in found.model
    assert found.host in found.label
    other = discovery.DiscoveredDisplay(host="192.168.1.155", settings_name="settings_ezi5xx.bin")
    assert "i550" in other.model


def test_unknown_settings_file_still_names_something(discovery):
    found = discovery.DiscoveredDisplay(host="1.2.3.4", settings_name="settings_future.bin")
    assert "future" in found.model


def test_positional_suffix_does_not_hide_the_unit(catalogue):
    # A row with two readings gets " 1" and " 2" appended, which used to push the
    # unit out of reach of the pattern that looks at the end of the name.
    assert catalogue._unit("%.1f", "Brine in/ut °C 1") == "°C"
    assert catalogue._clean_label("Brine in/ut °C 2") == "Brine in/ut 2"
    assert catalogue._base_label("Hetgas/Suggas °C 3") == "Hetgas/Suggas °C"


def test_readings_without_a_caption_are_left_unnamed(catalogue, web_api):
    # Schematic pages place readings on a diagram with no caption beside them.
    # Naming them after whatever string is nearest produces confident nonsense.
    widget = web_api.Widget
    widgets = [
        widget(index=0, kind=1, x=365, y=55, width=39, height=31, visible=True, label="Suomi"),
        widget(index=1, kind=3, x=420, y=55, width=55, height=33, visible=True, value_fmt="%.1f", value_vars=[16]),
    ]
    pairing = catalogue._pair_labels(widgets)
    assert 1 not in pairing


def test_pacing_holds_the_gap_between_transactions(modbus_api):
    # CTC documents an update rate for the BMS interface and cannot pipeline, so
    # the client must space requests out rather than send them back to back.
    import asyncio
    import time

    client = modbus_api.CtcModbusClient("192.168.1.55")

    async def scenario() -> float:
        client._last_request = time.monotonic()
        started = time.monotonic()
        await client._pace()
        return time.monotonic() - started

    waited = asyncio.run(scenario())
    assert waited >= modbus_api.MESSAGE_WAIT * 0.8


def test_pacing_does_not_wait_when_the_gap_has_passed(modbus_api):
    import asyncio
    import time

    client = modbus_api.CtcModbusClient("192.168.1.55")

    async def scenario() -> float:
        client._last_request = time.monotonic() - 10
        started = time.monotonic()
        await client._pace()
        return time.monotonic() - started

    assert asyncio.run(scenario()) < modbus_api.MESSAGE_WAIT
