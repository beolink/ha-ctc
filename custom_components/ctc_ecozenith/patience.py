"""How much slowness the display is allowed before it counts as gone.

The panel answers over a small embedded web server that is sometimes simply
slow, and a harvest walks it through several pages. Losing every reading over
one late answer is worse than showing the reading we already have: these are
lifetime counters and daily sums, not a live measurement.

Free of Home Assistant, so the rule can be tested on its own.
"""

from __future__ import annotations


class Patience:
    """Counts failed harvests and decides when to give up on the display."""

    def __init__(self, interval: int, retry_interval: int, limit: int = 3) -> None:
        self._interval = interval
        self._retry = min(retry_interval, interval)
        self._limit = limit
        self.failures = 0

    @property
    def seconds(self) -> int:
        """How long to wait before the next harvest."""
        return self._retry if self.failures else self._interval

    def failed(self, has_data: bool) -> bool:
        """Record a failed harvest. True when it should be shown as a failure.

        Nothing is hidden for long: the first failures keep the last readings
        and try again sooner, and once the display has been quiet this many
        times in a row it is reported as unavailable. With no reading to fall
        back on there is nothing to keep, so the failure is shown at once.
        """
        self.failures += 1
        return not has_data or self.failures >= self._limit

    def worked(self) -> None:
        self.failures = 0
