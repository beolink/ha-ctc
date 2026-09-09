"""Coefficient of performance over a rolling year.

Modbus reports what the unit consumes but never what it delivers, so a real
coefficient of performance is only possible with the display's two lifetime
counters: energy output total and energy consumption total.

Dividing those two gives the figure for the whole life of the machine, which
flatters or punishes it for years nobody is asking about. A yearly figure needs
the difference across a window, so one sample a day is kept and the oldest one
inside the window is used as the starting point. Until a year of samples exists
the lifetime figure is reported instead, and which of the two it is, is stated
rather than hidden.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from .const import COP_HISTORY_DAYS, COP_WINDOW_DAYS

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

#: Below this the divisor is noise rather than a measurement.
MIN_CONSUMPTION_KWH = 50.0


@dataclass
class CopResult:
    """A coefficient of performance and how it was arrived at."""

    value: float | None
    #: "year" once a full window is available, "lifetime" before that.
    basis: str
    days: int
    energy_out: float | None = None
    energy_in: float | None = None

    def as_attributes(self) -> dict[str, Any]:
        return {
            "underlag": "rullande år" if self.basis == "year" else "hela livslängden",
            "dygn i underlaget": self.days,
            "avgiven värme kWh": self.energy_out,
            "tillförd energi kWh": self.energy_in,
        }


def _ratio(out: float, consumed: float) -> float | None:
    if consumed < MIN_CONSUMPTION_KWH:
        return None
    return round(out / consumed, 2)


class CopTracker:
    """Keeps one sample a day of the two lifetime counters."""

    def __init__(self, store: Any) -> None:
        self._store = store
        self._samples: dict[str, list[float]] = {}
        self._loaded = False

    async def async_load(self) -> None:
        if self._loaded:
            return
        data = await self._store.async_load()
        if isinstance(data, dict) and isinstance(data.get("samples"), dict):
            self._samples = {
                day: [float(values[0]), float(values[1])]
                for day, values in data["samples"].items()
                if isinstance(values, (list, tuple)) and len(values) >= 2
            }
        self._loaded = True

    async def async_record(
        self, energy_out: float | None, energy_in: float | None, today: date | None = None
    ) -> None:
        """Store today's counters, replacing an earlier reading from today."""
        if energy_out is None or energy_in is None:
            return
        await self.async_load()
        stamp = (today or date.today()).isoformat()
        self._samples[stamp] = [float(energy_out), float(energy_in)]
        cutoff = ((today or date.today()) - timedelta(days=COP_HISTORY_DAYS)).isoformat()
        self._samples = {d: v for d, v in self._samples.items() if d >= cutoff}
        await self._store.async_save({"samples": self._samples})

    def result(
        self,
        energy_out: float | None,
        energy_in: float | None,
        today: date | None = None,
    ) -> CopResult:
        """Work out the rolling figure, falling back to the lifetime one."""
        if energy_out is None or energy_in is None:
            return CopResult(None, "lifetime", 0)

        now = today or date.today()
        window_start = (now - timedelta(days=COP_WINDOW_DAYS)).isoformat()
        older = sorted(d for d in self._samples if d <= window_start)
        if older:
            base_out, base_in = self._samples[older[-1]]
            span = (now - date.fromisoformat(older[-1])).days
            delta_out = energy_out - base_out
            delta_in = energy_in - base_in
            # A counter that went backwards means the unit was replaced or reset;
            # the lifetime figure is the only honest answer then.
            if delta_out >= 0 and delta_in >= 0:
                value = _ratio(delta_out, delta_in)
                if value is not None:
                    return CopResult(value, "year", span, round(delta_out, 1), round(delta_in, 1))

        oldest = min(self._samples) if self._samples else None
        span = (now - date.fromisoformat(oldest)).days if oldest else 0
        return CopResult(
            _ratio(energy_out, energy_in),
            "lifetime",
            span,
            round(energy_out, 1),
            round(energy_in, 1),
        )


def find_energy_totals(pages: list[Any]) -> tuple[Any | None, Any | None]:
    """Pick the two lifetime counters out of the harvested pages.

    Matched on the label the display itself printed, in English first and then
    in Swedish, so it works whichever language the panel is set to.
    """
    from .const import (
        LABEL_ENERGY_IN_EN,
        LABEL_ENERGY_IN_SV,
        LABEL_ENERGY_OUT_EN,
        LABEL_ENERGY_OUT_SV,
    )

    def match(value: Any, english: str, swedish: str) -> bool:
        # The catalogue strips a trailing unit from the row name, so the stored
        # label is "Avgiven värme totalt" rather than "... (kWh)".
        label = (value.label or "").strip().casefold()
        return label.startswith(english.casefold()) or label.startswith(swedish.casefold())

    out = None
    consumed = None
    for page in pages:
        for value in page.values:
            if out is None and match(value, LABEL_ENERGY_OUT_EN, LABEL_ENERGY_OUT_SV):
                out = value
            elif consumed is None and match(value, LABEL_ENERGY_IN_EN, LABEL_ENERGY_IN_SV):
                consumed = value
    return out, consumed
