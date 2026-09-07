"""What this integration contributes to the anonymous daily report.

Deliberately free of Home Assistant imports, so the unit tests can prove
without a Home Assistant installation that nothing but the agreed fields can
leave the house. Everything here is either a fixed slug from a closed list or
a plain count. No string that came from the unit, the network or the user is
ever passed through.

See https://stats.rnet.se/integritet for the full list and the reasoning.
"""

from __future__ import annotations

from typing import Any

#: Display family to a short slug. Unknown models report "other" rather than
#: their name, so a future model cannot leak an unreviewed string.
MODEL_SLUGS = {
    "EcoZenith i255": "i255",
    "EcoZenith i360": "i360",
    "EcoZenith i550 Pro": "i550",
    "EcoLogic": "ecologic",
}


def model_slug(model: str | None) -> str:
    """Map a discovered model name to a slug from the closed list above."""
    if not model:
        return "unknown"
    return MODEL_SLUGS.get(model.strip(), "other")


class ErrorCounter:
    """Turns a cumulative failure count into 'since the previous report'.

    A reload resets the coordinator's counter, so a value lower than the one
    seen last time means a fresh start, not a negative number of failures.
    """

    def __init__(self) -> None:
        self._seen = 0

    def delta(self, total: int) -> int:
        if total < self._seen:
            self._seen = 0
        change = total - self._seen
        self._seen = total
        return change


def build_extra(
    model: str | None,
    *,
    has_display: bool,
    control_enabled: bool,
    page_count: int,
    read_failures: int,
) -> dict[str, Any]:
    """Build the integration specific part of the daily report.

    ``page_count`` is how many display pages are harvested, not which ones:
    the page names come from the unit's own menu and could carry an installer's
    text.
    """
    return {
        "models": [model_slug(model)],
        "features": {
            # Modbus is always the base transport, the display is optional.
            "modbus": True,
            "display": bool(has_display),
            "control": bool(control_enabled),
            "pages": max(0, int(page_count)),
        },
        "errors": max(0, int(read_failures)),
    }
