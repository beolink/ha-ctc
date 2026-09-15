"""Give an installation's record of what it has seen a past, from recorded history.

seen.py learns what an installation has by watching its values, and an installation
that has run this integration before this record existed would otherwise start with
nothing: every reading that happens to be zero at that moment, a compressor at rest
or a heater not in use, would be left off the page until it next moved. Home
Assistant has usually kept statistics for these sensors all along, so the maximum
and minimum of each over the past year settle it at once, the first time.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .seen import SeenValues, is_zero

_LOGGER = logging.getLogger(__name__)

#: How far back a value counts as seen.
SEED_DAYS = 365


async def async_seed_from_statistics(
    hass: HomeAssistant, entry_id: str, prefix: str, seen: SeenValues
) -> int:
    """Mark as seen every sensor of this entry that recorded a value other than zero.

    Returns how many were added. Never raises: without a recorder, or with
    statistics it cannot read, the installation simply learns as it goes.
    """
    try:
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import statistics_during_period
        from homeassistant.util import dt as dt_util

        if "recorder" not in hass.config.components:
            return 0
        keys = {
            item.entity_id: item.unique_id[len(prefix):]
            for item in er.async_entries_for_config_entry(er.async_get(hass), entry_id)
            if item.domain == "sensor" and item.unique_id.startswith(prefix)
        }
        if not keys:
            return 0
        start = dt_util.utcnow() - timedelta(days=SEED_DAYS)
        rows: dict[str, list[Any]] = await get_instance(hass).async_add_executor_job(
            statistics_during_period,
            hass, start, None, set(keys), "month", None, {"max", "min", "state"},
        )
        found = {
            keys[statistic_id]
            for statistic_id, points in rows.items()
            if statistic_id in keys and any(
                point.get(kind) is not None and not is_zero(point.get(kind))
                for point in points for kind in ("max", "min", "state")
            )
        }
        added = seen.add(found)
        _LOGGER.debug("Took %d readings with a value from the recorded statistics", added)
        return added
    except Exception:  # noqa: BLE001 - history is a bonus, never a condition
        _LOGGER.debug("Could not look up the recorded statistics", exc_info=True)
        return 0
