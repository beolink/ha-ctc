"""Which readings this installation has ever given a value other than zero.

CTC's controller answers for hardware that is not fitted, and for the registers a
model or software revision does not use, with a clean zero: a brine pump on an air
to water heat pump, current sensors that were never installed, the refrigerant
circuit of a unit whose compressor has never run. What a given installation has is
therefore something only that installation can tell, by giving a value. Every value
the two coordinators read is noted here once it is a number other than zero, and the
CTC EcoZenith page leaves out a reading that is zero and has never been anything else.

Kept in a Store per entry, so a value seen once stays known across restarts. Free of
Home Assistant imports: the store is handed in, as for cop.CopTracker.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Mapping

_LOGGER = logging.getLogger(__name__)

#: How long a change to the set may wait before it is written down.
SAVE_DELAY_SECONDS = 60


def is_zero(value: Any) -> bool:
    """A number that is exactly zero. Anything else, text included, is a value."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value == 0
    if isinstance(value, str):
        try:
            return float(value) == 0
        except ValueError:
            return False
    return False


class SeenValues:
    """The keys that have had a value other than zero, ever."""

    def __init__(self, store: Any, on_new: Callable[[], None] | None = None) -> None:
        self._store = store
        self._on_new = on_new
        self.keys: set[str] = set()
        #: True until something was stored: an installation new to this, whose
        #: past values are worth looking up in the recorded history once.
        self.fresh = True

    async def async_load(self) -> None:
        stored = await self._store.async_load()
        if isinstance(stored, Mapping) and isinstance(stored.get("keys"), list):
            self.keys = {str(key) for key in stored["keys"]}
            self.fresh = False

    def note(self, data: Mapping[str, Any] | None) -> None:
        """Take in a coordinator's data, and remember what is new."""
        self.add(
            key for key, value in (data or {}).items()
            if value is not None and not is_zero(value)
        )

    def add(self, keys: Any) -> int:
        """Remember these keys as having had a value. Returns how many were new."""
        new = {str(key) for key in keys} - self.keys
        if not new:
            return 0
        self.keys |= new
        try:
            self._store.async_delay_save(lambda: {"keys": sorted(self.keys)}, SAVE_DELAY_SECONDS)
        except Exception as err:  # noqa: BLE001 - a lost save only means a later rediscovery
            _LOGGER.debug("Could not schedule saving the seen values: %s", err)
        if self._on_new is not None:
            self._on_new()
        return len(new)

    def unused(self, values: Mapping[str, Any]) -> set[str]:
        """Of these current values, the keys that are zero and never were anything else."""
        return {key for key, value in values.items() if key not in self.keys and is_zero(value)}
