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
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .const import COP_HISTORY_DAYS, COP_WINDOW_DAYS

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

#: Below this the divisor is noise rather than a measurement. A year needs a
#: real number behind it; a single day is allowed to work with much less, since
#: a day of heating is a few tens of kilowatt hours at most.
MIN_CONSUMPTION_KWH = 50.0
MIN_CONSUMPTION_KWH_DAY = 3.0

#: A sample has to be this old before it can serve as yesterday. The counters
#: are whole kilowatt hours, so a shorter span divides two small integers and
#: the answer swings wildly.
DAY_MIN_HOURS = 20
DAY_MAX_HOURS = 30

#: How long the short run of samples behind the daily figure is kept.
RECENT_DAYS = 4

#: A yearly figure has to stand on a span close to a year. A gap in the samples
#: must not quietly turn "the last year" into "the last fourteen months".
YEAR_MAX_DAYS = 380


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
        basis = {
            "year": "rullande år",
            "day": "senaste dygnet",
            "first_year": "första året",
            "lifetime": "hela livslängden",
        }.get(self.basis, self.basis)
        return {
            "underlag": basis,
            "dygn i underlaget": self.days,
            "avgiven värme kWh": self.energy_out,
            "tillförd energi kWh": self.energy_in,
        }


def _parse(stamp: str) -> datetime:
    """Read a stored timestamp, treating a naive one as UTC."""
    value = datetime.fromisoformat(stamp)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _ratio(out: float, consumed: float) -> float | None:
    if consumed < MIN_CONSUMPTION_KWH:
        return None
    return round(out / consumed, 2)


class CopTracker:
    """Keeps one sample a day of the two lifetime counters."""

    def __init__(self, store: Any) -> None:
        self._store = store
        self._samples: dict[str, list[float]] = {}
        #: A short run of timestamped samples. The daily figure needs finer
        #: spacing than one a day, or "yesterday" could be anything from 12 to
        #: 36 hours ago depending on when the samples happened to land.
        self._recent: list[tuple[str, float, float]] = []
        #: The day the lifetime counters started from zero, when known. CTC's
        #: counters begin at nothing the day the unit is commissioned, which
        #: makes that day a sample of its own: zero delivered, zero consumed.
        self._anchor: str | None = None
        #: The first year of the machine's life, kept once it has been seen.
        self._first_year: list[float] | None = None
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
        if isinstance(data, dict):
            anchor = data.get("anchor")
            self._anchor = anchor if isinstance(anchor, str) else None
            first = data.get("first_year")
            if isinstance(first, (list, tuple)) and len(first) >= 3:
                self._first_year = [float(first[0]), float(first[1]), float(first[2])]
        if isinstance(data, dict) and isinstance(data.get("recent"), list):
            for row in data["recent"]:
                if isinstance(row, (list, tuple)) and len(row) >= 3:
                    try:
                        self._recent.append((str(row[0]), float(row[1]), float(row[2])))
                    except (TypeError, ValueError):
                        continue
        self._loaded = True

    async def async_record(
        self,
        energy_out: float | None,
        energy_in: float | None,
        today: date | None = None,
        now: datetime | None = None,
    ) -> None:
        """Store the counters: one per day for the year, and a timestamped run."""
        if energy_out is None or energy_in is None:
            return
        await self.async_load()
        when = now or datetime.now(timezone.utc)
        stamp = (today or when.date()).isoformat()
        self._samples[stamp] = [float(energy_out), float(energy_in)]
        cutoff = ((today or when.date()) - timedelta(days=COP_HISTORY_DAYS)).isoformat()
        self._samples = {d: v for d, v in self._samples.items() if d >= cutoff}

        self._recent.append((when.isoformat(), float(energy_out), float(energy_in)))
        keep = when - timedelta(days=RECENT_DAYS)
        self._recent = [r for r in self._recent if _parse(r[0]) >= keep]

        self._capture_first_year(when.date())
        await self._async_save()

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                "samples": self._samples,
                "recent": self._recent,
                "anchor": self._anchor,
                "first_year": self._first_year,
            }
        )

    async def async_set_anchor(self, commissioned: date) -> None:
        """Record the day the counters started from zero.

        Only ever moved earlier, never later: the operating hours it is worked
        out from stop counting while the unit is switched off, so the earliest
        answer seen is the closest to the truth.
        """
        await self.async_load()
        stamp = commissioned.isoformat()
        if self._anchor is None or stamp < self._anchor:
            self._anchor = stamp
            await self._async_save()

    @property
    def anchor(self) -> date | None:
        return date.fromisoformat(self._anchor) if self._anchor else None

    def _capture_first_year(self, today: date) -> None:
        """Keep the first year's figure once a sample from its end exists.

        The counters were zero on the anchor day, so a sample taken about a year
        later holds the whole first year on its own. It is kept for good: the
        machine only ever has one first year.
        """
        if self._first_year is not None or self._anchor is None:
            return
        start = date.fromisoformat(self._anchor)
        for day in sorted(self._samples):
            span = (date.fromisoformat(day) - start).days
            if COP_WINDOW_DAYS <= span <= YEAR_MAX_DAYS:
                out, consumed = self._samples[day]
                self._first_year = [float(out), float(consumed), float(span)]
                return

    def result_first_year(self) -> CopResult:
        """The machine's first year, or nothing until it has had one."""
        if self._first_year is None:
            return CopResult(None, "first_year", 0)
        out, consumed, span = self._first_year
        return CopResult(_ratio(out, consumed), "first_year", int(span), round(out, 1), round(consumed, 1))

    def result_day(
        self,
        energy_out: float | None,
        energy_in: float | None,
        now: datetime | None = None,
    ) -> CopResult:
        """The figure over the last day, from the newest sample old enough to be
        yesterday. Nothing is returned until such a sample exists."""
        if energy_out is None or energy_in is None:
            return CopResult(None, "day", 0)
        when = now or datetime.now(timezone.utc)
        oldest_allowed = when - timedelta(hours=DAY_MAX_HOURS)
        newest_allowed = when - timedelta(hours=DAY_MIN_HOURS)
        window = [
            r for r in self._recent if oldest_allowed <= _parse(r[0]) <= newest_allowed
        ]
        if not window:
            return CopResult(None, "day", 0)
        stamp, base_out, base_in = max(window, key=lambda r: _parse(r[0]))
        delta_out = energy_out - base_out
        delta_in = energy_in - base_in
        if delta_out < 0 or delta_in < 0:
            return CopResult(None, "day", 0)
        hours = (when - _parse(stamp)).total_seconds() / 3600
        if delta_in < MIN_CONSUMPTION_KWH_DAY:
            return CopResult(None, "day", round(hours / 24), round(delta_out, 1), round(delta_in, 1))
        return CopResult(
            round(delta_out / delta_in, 2),
            "day",
            max(1, round(hours / 24)),
            round(delta_out, 1),
            round(delta_in, 1),
        )

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
        earliest = (now - timedelta(days=YEAR_MAX_DAYS)).isoformat()
        candidates = dict(self._samples)
        if self._anchor is not None:
            candidates.setdefault(self._anchor, [0.0, 0.0])
        older = sorted(d for d in candidates if earliest <= d <= window_start)
        if older:
            base_out, base_in = candidates[older[-1]]
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



def find_operating_hours(pages: list[Any]) -> Any | None:
    """Pick the unit's total powered-on hours out of the harvested pages.

    Two rows look alike, "Total drifttid" for the hours the unit has been
    powered and "Drifttid total" for the compressor alone, and in English they
    are both "Total operation time". Powered-on hours can never be fewer than
    compressor hours, so of the rows that match, the largest one is the right
    one; the choice is made at read time, from the values themselves.
    """
    matches = []
    for page in pages:
        for value in page.values:
            label = (value.label or "").strip().casefold()
            if (value.unit or "") != "h":
                continue
            if label.startswith("total drifttid") or label.startswith("total operation time"):
                matches.append(value)
    return matches or None
