"""The path from a running installation to the daily report.

These exist because that path once broke without a single visible error: the
COP helper gained a fourth figure, the caller still unpacked three, the
resulting exception was swallowed by the reporter as it should be, and every
report for a day went out with no models, no firmware and no measurements.
The unit tests for each half passed; only the join between them was wrong.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import date, timedelta
from types import SimpleNamespace


class FakeStore:
    def __init__(self) -> None:
        self.data = None

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data


def _value(key, label):
    return SimpleNamespace(key=key, label=label)


def _runtime(cop, with_tracker: bool):
    tracker = cop.CopTracker(FakeStore()) if with_tracker else None
    web = SimpleNamespace(data={"out": 22499.0, "in": 9116.0}) if with_tracker else None
    return SimpleNamespace(
        cop=tracker,
        web=web,
        energy_out=_value("out", "Avgiven värme totalt") if with_tracker else None,
        energy_in=_value("in", "Tillförd energi totalt") if with_tracker else None,
    )


def test_every_figure_is_a_parameter_of_the_report_builder(cop, stats_extra):
    accepted = set(inspect.signature(stats_extra.build_extra).parameters)
    missing = [key for key in cop.COP_REPORT_KEYS if key not in accepted]
    assert not missing, f"build_extra tar inte emot {missing}"


def test_an_installation_without_figures_still_reports_its_models(cop, stats_extra):
    # V1: no display history, so no tracker. The report must still carry the
    # model and the firmware rather than dropping everything.
    runtime = _runtime(cop, with_tracker=False)
    payload = stats_extra.build_extra(
        "EcoZenith i550 Pro",
        has_display=True,
        control_enabled=False,
        page_count=1,
        read_failures=0,
        control_firmware=925,
        **cop.cop_for_report(runtime),
    )
    assert payload["models"] == ["i550"]
    assert payload["firmwares"] == {"control": "925"}


def test_an_installation_with_figures_reports_them(cop, stats_extra):
    # VSH: the history page is harvested, so there is a lifetime figure.
    runtime = _runtime(cop, with_tracker=True)
    payload = stats_extra.build_extra(
        "EcoZenith i255",
        has_display=True,
        control_enabled=False,
        page_count=2,
        read_failures=0,
        heatpump_model="EA720M",
        serial="720825408489",
        **cop.cop_for_report(runtime),
    )
    assert payload["models"] == ["i255", "ea720m"]
    assert payload["metrics"]["cop_lifetime"] == 2.47
    assert payload["metrics"]["built_year"] == 2025
    assert payload["metrics"]["product_code"] == 7208


def test_the_first_year_travels_all_the_way_to_the_report(cop, stats_extra):
    runtime = _runtime(cop, with_tracker=True)
    commissioned = date(2025, 10, 10)
    asyncio.run(runtime.cop.async_set_anchor(commissioned))
    asyncio.run(
        runtime.cop.async_record(24000, 9700, today=commissioned + timedelta(days=366))
    )
    payload = stats_extra.build_extra(
        "EcoZenith i255",
        has_display=True,
        control_enabled=False,
        page_count=2,
        read_failures=0,
        **cop.cop_for_report(runtime),
    )
    assert payload["metrics"]["cop_first_year"] == round(24000 / 9700, 2)


def test_figures_are_a_mapping_not_a_tuple(cop):
    # A tuple is what broke: a new figure silently shifts every unpacking.
    figures = cop.cop_for_report(SimpleNamespace(cop=None, web=None))
    assert isinstance(figures, dict)
    assert set(figures) == set(cop.COP_REPORT_KEYS)


def test_nothing_unpacks_the_figures_as_a_tuple():
    # __init__.py needs Home Assistant and cannot be imported here, so the call
    # site is checked as source. Unpacking cop_for_report into names is exactly
    # what broke; passing it on as keyword arguments cannot break that way.
    import ast
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parent.parent
        / "custom_components" / "ctc_ecozenith" / "__init__.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name == "cop_for_report" and any(
                isinstance(target, ast.Tuple) for target in node.targets
            ):
                offenders.append(node.lineno)
    assert not offenders, f"cop_for_report packas upp på rad {offenders}"
    assert "**cop_for_report(" in source, "rapporten får inte värmefaktorerna"
