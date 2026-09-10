"""Tests for the rolling coefficient of performance."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest


class FakeStore:
    """Stands in for Home Assistant's Store, in memory."""

    def __init__(self) -> None:
        self.data = None

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data


def run(coro):
    return asyncio.run(coro)


def test_lifetime_ratio_before_a_year_of_samples(cop):
    tracker = cop.CopTracker(FakeStore())
    result = tracker.result(22421, 9088, today=date(2026, 9, 9))
    assert result.basis == "lifetime"
    assert result.value == pytest.approx(2.47, abs=0.01)


def test_rolling_year_uses_the_difference(cop):
    tracker = cop.CopTracker(FakeStore())
    today = date(2026, 9, 9)
    a_year_ago = today - timedelta(days=366)
    run(tracker.async_record(18000, 7500, today=a_year_ago))
    result = tracker.result(22421, 9088, today=today)
    assert result.basis == "year"
    # 4421 kWh delivered against 1588 consumed over the year.
    assert result.value == pytest.approx(2.78, abs=0.01)
    assert result.energy_out == pytest.approx(4421, abs=1)


def test_a_sample_inside_the_window_is_not_used(cop):
    tracker = cop.CopTracker(FakeStore())
    today = date(2026, 9, 9)
    run(tracker.async_record(21000, 8600, today=today - timedelta(days=100)))
    result = tracker.result(22421, 9088, today=today)
    assert result.basis == "lifetime"


def test_counters_going_backwards_fall_back_to_lifetime(cop):
    # A replaced unit or a reset counter must not produce a negative delta.
    tracker = cop.CopTracker(FakeStore())
    today = date(2026, 9, 9)
    run(tracker.async_record(30000, 12000, today=today - timedelta(days=400)))
    result = tracker.result(22421, 9088, today=today)
    assert result.basis == "lifetime"


def test_too_little_consumption_is_not_a_measurement(cop):
    tracker = cop.CopTracker(FakeStore())
    assert tracker.result(30, 10, today=date(2026, 9, 9)).value is None


def test_missing_counters_give_nothing(cop):
    tracker = cop.CopTracker(FakeStore())
    assert tracker.result(None, 9088).value is None
    assert tracker.result(22421, None).value is None


def test_samples_older_than_the_history_are_dropped(cop):
    store = FakeStore()
    tracker = cop.CopTracker(store)
    today = date(2026, 9, 9)
    run(tracker.async_record(1000, 400, today=today - timedelta(days=500)))
    run(tracker.async_record(22421, 9088, today=today))
    assert (today - timedelta(days=500)).isoformat() not in store.data["samples"]
    assert today.isoformat() in store.data["samples"]


def test_one_sample_a_day_replaces_the_earlier_one(cop):
    store = FakeStore()
    tracker = cop.CopTracker(store)
    today = date(2026, 9, 9)
    run(tracker.async_record(22000, 9000, today=today))
    run(tracker.async_record(22421, 9088, today=today))
    assert store.data["samples"][today.isoformat()] == [22421.0, 9088.0]


def test_samples_survive_a_restart(cop):
    store = FakeStore()
    today = date(2026, 9, 9)
    run(cop.CopTracker(store).async_record(18000, 7500, today=today - timedelta(days=366)))

    revived = cop.CopTracker(store)
    run(revived.async_load())
    assert revived.result(22421, 9088, today=today).basis == "year"


def test_energy_totals_are_found_by_label(cop, const):
    def value(label):
        return const.SlowValue(
            key=label, label=label, page=30, screen=128, fmt="%d", var_indices=[1], unit="kWh"
        )

    page = const.SlowPage(page=30, title="Historik", screens=[128])
    page.values = [
        value("Total drifttid"),
        value("Avgiven värme totalt"),
        value("Tillförd energi totalt"),
    ]
    out, consumed = cop.find_energy_totals([page])
    assert out is not None and out.label == "Avgiven värme totalt"
    assert consumed is not None and consumed.label == "Tillförd energi totalt"


def test_energy_totals_are_found_in_english_too(cop, const):
    def value(label):
        return const.SlowValue(
            key=label, label=label, page=30, screen=128, fmt="%d", var_indices=[1], unit="kWh"
        )

    page = const.SlowPage(page=30, title="History", screens=[128])
    page.values = [value("Energy output total"), value("Energy consumption total")]
    out, consumed = cop.find_energy_totals([page])
    assert out is not None and out.label == "Energy output total"
    assert consumed is not None and consumed.label == "Energy consumption total"


def test_energy_totals_absent_when_the_page_is_not_harvested(cop, const):
    page = const.SlowPage(page=22, title="Värmepump", screens=[118])
    page.values = [
        const.SlowValue(key="k", label="Hetgas", page=22, screen=118, fmt="%.1f", var_indices=[1])
    ]
    assert cop.find_energy_totals([page]) == (None, None)


def test_manufacturing_date_comes_out_of_the_serial(identity):
    unit = identity.Identity(serial="720825408489")
    assert unit.manufactured == "2025 vecka 40"
    # CTC's own example from their serial number guide.
    assert identity.Identity(serial="731217120719").manufactured == "2017 vecka 12"


def test_a_serial_that_is_not_one_gives_no_date(identity):
    assert identity.Identity(serial="7208").manufactured is None
    assert identity.Identity(serial=None).manufactured is None
    assert identity.Identity(serial="720825998489").manufactured is None


# ------------------------------------------------------------- dygnsvärdet

from datetime import datetime, timezone


def test_daily_figure_uses_a_sample_from_yesterday(cop):
    tracker = cop.CopTracker(FakeStore())
    now = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
    run(tracker.async_record(22400, 9100, now=now - timedelta(hours=24)))
    result = tracker.result_day(22440, 9112, now=now)
    assert result.basis == "day"
    assert result.value == pytest.approx(40 / 12, abs=0.01)
    assert result.energy_out == pytest.approx(40, abs=0.1)


def test_a_sample_that_is_too_fresh_is_not_yesterday(cop):
    # Dividing two small integers a few hours apart swings wildly.
    tracker = cop.CopTracker(FakeStore())
    now = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
    run(tracker.async_record(22400, 9100, now=now - timedelta(hours=6)))
    assert tracker.result_day(22440, 9112, now=now).value is None


def test_a_sample_that_is_too_old_is_not_yesterday_either(cop):
    tracker = cop.CopTracker(FakeStore())
    now = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
    run(tracker.async_record(22400, 9100, now=now - timedelta(hours=48)))
    assert tracker.result_day(22440, 9112, now=now).value is None


def test_a_still_day_gives_no_daily_figure(cop):
    # A day the pump barely ran divides almost nothing by almost nothing.
    tracker = cop.CopTracker(FakeStore())
    now = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
    run(tracker.async_record(22400, 9100, now=now - timedelta(hours=24)))
    assert tracker.result_day(22402, 9101, now=now).value is None


def test_a_counter_reset_gives_no_daily_figure(cop):
    tracker = cop.CopTracker(FakeStore())
    now = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
    run(tracker.async_record(30000, 12000, now=now - timedelta(hours=24)))
    assert tracker.result_day(100, 40, now=now).value is None


def test_the_daily_run_is_pruned_but_the_yearly_map_is_not(cop):
    store = FakeStore()
    tracker = cop.CopTracker(store)
    now = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
    run(tracker.async_record(20000, 8000, now=now - timedelta(days=10)))
    run(tracker.async_record(22440, 9112, now=now))
    assert len(store.data["recent"]) == 1
    assert len(store.data["samples"]) == 2


def test_the_daily_run_survives_a_restart(cop):
    store = FakeStore()
    now = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
    run(cop.CopTracker(store).async_record(22400, 9100, now=now - timedelta(hours=24)))
    revived = cop.CopTracker(store)
    run(revived.async_load())
    assert revived.result_day(22440, 9112, now=now).basis == "day"
    assert revived.result_day(22440, 9112, now=now).value is not None


def test_an_old_store_without_the_daily_run_still_loads(cop):
    store = FakeStore()
    store.data = {"samples": {"2026-09-09": [22400.0, 9100.0]}}
    tracker = cop.CopTracker(store)
    run(tracker.async_load())
    assert tracker.result_day(22440, 9112).value is None
    assert tracker.result(22440, 9112).basis == "lifetime"



# ------------------------------------------------------ ankaret och första året


def test_fourteen_months_is_not_a_year(cop):
    # A gap in the samples must not quietly stretch "the last year".
    tracker = cop.CopTracker(FakeStore())
    today = date(2026, 9, 9)
    run(tracker.async_record(18000, 7500, today=today - timedelta(days=430)))
    assert tracker.result(22421, 9088, today=today).basis == "lifetime"


def test_the_commissioning_day_counts_as_a_zero_sample(cop):
    # CTC's counters start from nothing the day the unit is commissioned, so a
    # year after that the whole lifetime is exactly one year.
    tracker = cop.CopTracker(FakeStore())
    commissioned = date(2025, 10, 10)
    run(tracker.async_set_anchor(commissioned))
    anniversary = commissioned + timedelta(days=366)
    result = tracker.result(24000, 9700, today=anniversary)
    assert result.basis == "year"
    assert result.value == pytest.approx(24000 / 9700, abs=0.01)


def test_the_anchor_is_only_moved_earlier(cop):
    # Operating hours stop while the unit is off, so the earliest start seen is
    # the closest to the truth and a later reading must not replace it.
    store = FakeStore()
    tracker = cop.CopTracker(store)
    run(tracker.async_set_anchor(date(2025, 10, 10)))
    run(tracker.async_set_anchor(date(2025, 10, 20)))
    assert tracker.anchor == date(2025, 10, 10)
    run(tracker.async_set_anchor(date(2025, 10, 1)))
    assert tracker.anchor == date(2025, 10, 1)


def test_the_first_year_is_captured_and_kept(cop):
    store = FakeStore()
    tracker = cop.CopTracker(store)
    commissioned = date(2025, 10, 10)
    run(tracker.async_set_anchor(commissioned))
    assert tracker.result_first_year().value is None

    anniversary = commissioned + timedelta(days=366)
    run(tracker.async_record(24000, 9700, today=anniversary))
    first = tracker.result_first_year()
    assert first.basis == "first_year"
    assert first.value == pytest.approx(24000 / 9700, abs=0.01)

    # A later year does not overwrite the first one.
    run(tracker.async_record(46000, 18900, today=anniversary + timedelta(days=365)))
    assert tracker.result_first_year().value == pytest.approx(24000 / 9700, abs=0.01)


def test_the_first_year_survives_a_restart(cop):
    store = FakeStore()
    commissioned = date(2025, 10, 10)
    tracker = cop.CopTracker(store)
    run(tracker.async_set_anchor(commissioned))
    run(tracker.async_record(24000, 9700, today=commissioned + timedelta(days=366)))

    revived = cop.CopTracker(store)
    run(revived.async_load())
    assert revived.anchor == commissioned
    assert revived.result_first_year().value == pytest.approx(24000 / 9700, abs=0.01)


def test_no_first_year_before_the_anniversary(cop):
    tracker = cop.CopTracker(FakeStore())
    commissioned = date(2025, 10, 10)
    run(tracker.async_set_anchor(commissioned))
    run(tracker.async_record(22499, 9116, today=date(2026, 9, 10)))
    assert tracker.result_first_year().value is None
    assert tracker.result(22499, 9116, today=date(2026, 9, 10)).basis == "lifetime"


def test_powered_on_hours_are_told_apart_from_compressor_hours(cop, const):
    # "Total drifttid" and "Drifttid total" look alike, and in English both are
    # "Total operation time". Both are candidates; the caller takes the larger.
    def value(label, key):
        return const.SlowValue(key=key, label=label, page=30, screen=128,
                               fmt="%d", var_indices=[1], unit="h")
    page = const.SlowPage(page=30, title="Historik", screens=[128])
    page.values = [value("Total drifttid", "a"), value("Drifttid total", "b"),
                   value("Timer avfrostning", "c")]
    found = cop.find_operating_hours([page])
    assert [v.key for v in found] == ["a"]


def test_no_operating_hours_without_the_history_page(cop, const):
    page = const.SlowPage(page=22, title="Värmepump", screens=[118])
    assert cop.find_operating_hours([page]) is None


def test_a_later_read_fills_gaps_but_never_wipes(identity):
    # The display writes these values the first time their screen is shown,
    # so a read before that finds nothing and must not erase what is known.
    known = identity.Identity(serial="720825408489", display_firmware="20260610")
    empty = identity.Identity()
    assert known.merged_with(empty).display_firmware == "20260610"

    # A value that was actually read wins: firmware gets updated.
    later = identity.Identity(heatpump_model="EA720M", display_firmware="20270101")
    merged = known.merged_with(later)
    assert merged.heatpump_model == "EA720M"
    assert merged.display_firmware == "20270101", "en uppdaterad firmware ska synas"
    assert merged.serial == "720825408489", "det som inte lästes om ska ligga kvar"


def test_complete_only_when_every_field_is_known(identity):
    partial = identity.Identity(serial="720825408489")
    assert not partial.is_complete
    full = identity.Identity(serial="1", mac="2", display_firmware="3",
                             bootloader="4", heatpump_model="5", heatpump_firmware="6")
    assert full.is_complete
