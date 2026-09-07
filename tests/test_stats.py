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
