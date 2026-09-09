"""What this integration contributes to the anonymous daily report.

Deliberately free of Home Assistant imports, so the unit tests can prove
without a Home Assistant installation that nothing but the agreed fields can
leave the house. Everything here is either a fixed slug from a closed list or
a plain count. No string that came from the unit, the network or the user is
ever passed through.

See https://stats.rnet.se/integritet for the full list and the reasoning.
"""

from __future__ import annotations

import re
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


#: CTC names its outdoor units EA or EP followed by three digits and sometimes
#: an M. Matching the shape rather than keeping a list means a model released
#: tomorrow still reports as itself, while anything else reports as "other", so
#: no free text can reach the database.
HEATPUMP_PATTERN = re.compile(r"^(EA|EP)\d{3}M?$", re.I)


def heatpump_slug(model: str | None) -> str:
    """Map the outdoor unit's name to a slug, or "other"."""
    if not model:
        return "unknown"
    cleaned = model.strip()
    return cleaned.lower() if HEATPUMP_PATTERN.match(cleaned) else "other"


#: Firmware written as a date, which is how both the display and the heat pump
#: control board report theirs.
FIRMWARE_PATTERN = re.compile(r"^\d{8}$")


def firmware_value(raw: Any) -> str | None:
    """Keep a firmware only if it is the eight digit date CTC writes."""
    if raw is None:
        return None
    text = str(raw).strip()
    return text if FIRMWARE_PATTERN.match(text) else None


def control_firmware_value(raw: Any) -> int | None:
    """The control unit reports its software as a plain number."""
    if raw is None:
        return None
    try:
        number = int(raw)
    except (TypeError, ValueError):
        return None
    return number if 0 < number < 100000 else None


#: CTC writes a serial number as three groups of four digits: which product it
#: is, the year and week it was made, and a sequence number. Only the first two
#: groups are reported. They are shared by a whole production run, so they say
#: when a machine was built without saying which machine it is. The sequence
#: number, which does identify it, never leaves the house.
#: https://ctc.se/blogg/varmepump/guide-for-produktens-serienummer
SERIAL_GROUP = 4


def _serial_digits(raw: Any) -> str | None:
    if raw is None:
        return None
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    return digits if len(digits) >= SERIAL_GROUP * 3 else None


def serial_product(raw: Any) -> str | None:
    """The first group: which product this is."""
    digits = _serial_digits(raw)
    return digits[:SERIAL_GROUP] if digits else None


def serial_made(raw: Any) -> str | None:
    """The second group: the year and week it was made, as YYWW.

    Rejected unless the week is a real one, so a serial in some other format
    cannot be read as a date that never existed.
    """
    digits = _serial_digits(raw)
    if not digits:
        return None
    made = digits[SERIAL_GROUP : SERIAL_GROUP * 2]
    week = int(made[2:])
    return made if 1 <= week <= 53 else None


def cop_value(raw: Any) -> float | None:
    """A coefficient of performance, rejected unless it is physically sane."""
    if raw is None:
        return None
    try:
        value = round(float(raw), 2)
    except (TypeError, ValueError):
        return None
    return value if 0.5 <= value <= 10.0 else None


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
    heatpump_model: str | None = None,
    serial: Any = None,
    display_firmware: Any = None,
    heatpump_firmware: Any = None,
    control_firmware: Any = None,
    cop_year: Any = None,
    cop_lifetime: Any = None,
) -> dict[str, Any]:
    """Build the integration specific part of the daily report.

    ``page_count`` is how many display pages are harvested, not which ones:
    the page names come from the unit's own menu and could carry an installer's
    text.
    """
    payload: dict[str, Any] = {
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

    # What the installation is made of, and how well it performs. None of this
    # identifies anyone: the serial number is deliberately not among it, even
    # though the integration knows it.
    hardware = {
        "heatpump": heatpump_slug(heatpump_model),
        "product": serial_product(serial),
        "made": serial_made(serial),
        "display_fw": firmware_value(display_firmware),
        "heatpump_fw": firmware_value(heatpump_firmware),
        "control_fw": control_firmware_value(control_firmware),
    }
    hardware = {k: v for k, v in hardware.items() if v is not None and v != "unknown"}
    if hardware:
        payload["hardware"] = hardware

    performance = {
        "cop_year": cop_value(cop_year),
        "cop_lifetime": cop_value(cop_lifetime),
    }
    performance = {k: v for k, v in performance.items() if v is not None}
    if performance:
        payload["performance"] = performance

    return payload
