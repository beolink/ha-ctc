"""Tests for what the optional daily report may contain.

The point of these is not that the numbers are right, it is that nothing but
the agreed fields can ever leave the house. stats_extra.py is deliberately free
of Home Assistant imports so this can be checked without installing HA.
"""

from __future__ import annotations

import pytest


# ------------------------------------------------------------------ models


def test_known_models_become_short_slugs(stats_extra):
    assert stats_extra.model_slug("EcoZenith i255") == "i255"
    assert stats_extra.model_slug("EcoZenith i550 Pro") == "i550"
    assert stats_extra.model_slug("EcoLogic") == "ecologic"


def test_unknown_model_never_leaks_its_name(stats_extra):
    # A future model, or the manual setup path where nothing was discovered.
    assert stats_extra.model_slug("CTC (ezi9xx)") == "other"
    assert stats_extra.model_slug("Villagatan 4") == "other"
    assert stats_extra.model_slug(None) == "unknown"
    assert stats_extra.model_slug("") == "unknown"


# ------------------------------------------------------------------ payload


def _extra(stats_extra, **kwargs):
    args = {
        "has_display": True,
        "control_enabled": False,
        "page_count": 5,
        "read_failures": 0,
    }
    args.update(kwargs)
    return stats_extra.build_extra("EcoZenith i550 Pro", **args)


def test_report_holds_only_the_agreed_keys(stats_extra):
    extra = _extra(stats_extra)
    assert set(extra) == {"models", "features", "errors"}
    assert set(extra["features"]) == {"modbus", "display", "control", "pages"}


def test_report_carries_no_free_text(stats_extra):
    extra = _extra(stats_extra)
    assert extra["models"] == ["i550"]
    for value in extra["features"].values():
        assert isinstance(value, (bool, int))


def test_page_count_is_a_number_not_the_page_names(stats_extra):
    # Page names come from the unit's own menu and can carry installer text.
    extra = _extra(stats_extra, page_count=3)
    assert extra["features"]["pages"] == 3


def test_transports_and_control_are_reported_as_they_are(stats_extra):
    off = _extra(stats_extra, has_display=False, control_enabled=False, page_count=0)
    assert off["features"] == {"modbus": True, "display": False, "control": False, "pages": 0}
    on = _extra(stats_extra, has_display=True, control_enabled=True)
    assert on["features"]["display"] is True
    assert on["features"]["control"] is True


def test_negative_counts_can_not_appear(stats_extra):
    extra = _extra(stats_extra, page_count=-2, read_failures=-7)
    assert extra["features"]["pages"] == 0
    assert extra["errors"] == 0


# ------------------------------------------------------------ error counter


def test_error_counter_reports_the_change_since_last_time(stats_extra):
    counter = stats_extra.ErrorCounter()
    assert counter.delta(0) == 0
    assert counter.delta(3) == 3
    assert counter.delta(3) == 0
    assert counter.delta(10) == 7


def test_error_counter_survives_a_reload(stats_extra):
    # A reload starts the coordinator's counter over at zero. Without this the
    # report would carry a negative number of failures.
    counter = stats_extra.ErrorCounter()
    counter.delta(12)
    assert counter.delta(2) == 2


# --------------------------------------------------- hårdvara och prestanda


def test_heatpump_slug_accepts_ctc_shapes(stats_extra):
    assert stats_extra.heatpump_slug("EA720M") == "ea720m"
    assert stats_extra.heatpump_slug("EP612M") == "ep612m"
    assert stats_extra.heatpump_slug("EA614") == "ea614"


def test_heatpump_slug_refuses_free_text(stats_extra):
    # A name the installer typed must never reach the database.
    assert stats_extra.heatpump_slug("Villan hos Andrei") == "other"
    assert stats_extra.heatpump_slug("EA720M; DROP TABLE") == "other"
    assert stats_extra.heatpump_slug("") == "unknown"
    assert stats_extra.heatpump_slug(None) == "unknown"


def test_serial_splits_into_product_and_build_week(stats_extra):
    # CTC's own example: 7312-1712-0719 is an EcoAir 510M from 2017 week 12.
    assert stats_extra.serial_product("731217120719") == "7312"
    assert stats_extra.serial_made("731217120719") == "1712"
    assert stats_extra.serial_product("720825408489") == "7208"
    assert stats_extra.serial_made("720825408489") == "2540"


def test_serial_needs_all_three_groups(stats_extra):
    assert stats_extra.serial_product("7208") is None
    assert stats_extra.serial_made("72082540") is None
    assert stats_extra.serial_product(None) is None


def test_an_impossible_week_is_not_a_build_date(stats_extra):
    assert stats_extra.serial_made("720825998489") is None
    assert stats_extra.serial_made("720825008489") is None


def test_the_sequence_number_never_leaves(stats_extra):
    full = "720825408489"
    reported = f"{stats_extra.serial_product(full)}{stats_extra.serial_made(full)}"
    assert "8489" not in reported


def test_firmware_must_be_a_date(stats_extra):
    assert stats_extra.firmware_value("20260610") == "20260610"
    assert stats_extra.firmware_value(20260522) == "20260522"
    assert stats_extra.firmware_value("2.1") is None
    assert stats_extra.firmware_value("hej") is None
    assert stats_extra.firmware_value(None) is None


def test_control_firmware_is_a_plain_number(stats_extra):
    assert stats_extra.control_firmware_value(925) == 925
    assert stats_extra.control_firmware_value("925") == 925
    assert stats_extra.control_firmware_value(0) is None
    assert stats_extra.control_firmware_value("x") is None


def test_cop_rejects_impossible_values(stats_extra):
    assert stats_extra.cop_value(3.62) == 3.62
    assert stats_extra.cop_value("2.5") == 2.5
    assert stats_extra.cop_value(0.1) is None
    assert stats_extra.cop_value(99) is None
    assert stats_extra.cop_value(None) is None


def test_payload_omits_what_is_not_known(stats_extra):
    payload = stats_extra.build_extra(
        "EcoZenith i255",
        has_display=False,
        control_enabled=False,
        page_count=0,
        read_failures=0,
    )
    assert "hardware" not in payload
    assert "performance" not in payload


def test_payload_carries_hardware_and_performance(stats_extra):
    payload = stats_extra.build_extra(
        "EcoZenith i255",
        has_display=True,
        control_enabled=False,
        page_count=1,
        read_failures=0,
        heatpump_model="EA720M",
        serial="720825408489",
        display_firmware="20260610",
        heatpump_firmware="20260522",
        control_firmware=925,
        cop_year=3.4,
        cop_lifetime=2.47,
    )
    assert payload["hardware"] == {
        "heatpump": "ea720m",
        "product": "7208",
        "made": "2540",
        "display_fw": "20260610",
        "heatpump_fw": "20260522",
        "control_fw": 925,
    }
    assert payload["performance"] == {"cop_year": 3.4, "cop_lifetime": 2.47}


def test_payload_never_carries_a_full_serial(stats_extra):
    payload = stats_extra.build_extra(
        "EcoZenith i255",
        has_display=True,
        control_enabled=False,
        page_count=1,
        read_failures=0,
        serial="720825408489",
    )
    assert "720825408489" not in repr(payload)
    assert "8489" not in repr(payload)
    assert payload["hardware"]["product"] == "7208"
    assert payload["hardware"]["made"] == "2540"
