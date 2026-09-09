"""Read what the unit is: model, serial and the firmware in each board.

None of this comes from Modbus. The display knows it and writes it into two of
its own screens, and unlike the readings, these values are static: they are
written once and stay put, so they can be read whatever page the panel happens
to be showing. That matters, because it means the identity can be read without
walking the panel through its menus.

Screen numbering differs between models, so the screens are located by
fingerprint rather than by number:

* The system information screen is the only one carrying several string
  values, which are the serial number, the MAC address, the display's own
  program version and its bootloader version.
* The heat pump screen carries the outdoor unit's model as plain text and its
  control board's software as a date coded number such as 20260522.

Once found, the rows are matched on their English labels, which are the same on
an i255 and an i550 Pro even though the text ids behind them are not.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

from .web_api import CtcWebClient, CtcWebError, Widget

_LOGGER = logging.getLogger(__name__)

#: English labels on the system information screen.
LABEL_SERIAL = "Serial number"
LABEL_MAC = "MAC address"
LABEL_PROGRAM = "Program version"
LABEL_BOOTLOADER = "Bootloader version"

#: English labels on the heat pump's operation data screen.
LABEL_MODEL = "Model"
LABEL_HP_SOFTWARE = "Software HP PCB"

#: A firmware written as a date. Anything outside this range is not one.
FIRMWARE_MIN = 20000000
FIRMWARE_MAX = 21000000

#: Rows on the history screen, by English label.
LABEL_ENERGY_OUT = "Energy output total (kWh)"
LABEL_ENERGY_IN = "Energy consumption total (kWh)"

_ROW_TOLERANCE = 14


@dataclass
class Identity:
    """What the unit says about itself."""

    serial: str | None = None
    mac: str | None = None
    display_firmware: str | None = None
    bootloader: str | None = None
    heatpump_model: str | None = None
    heatpump_firmware: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, stored: dict[str, Any] | None) -> "Identity":
        stored = stored or {}
        return cls(
            **{
                field: stored.get(field)
                for field in (
                    "serial",
                    "mac",
                    "display_firmware",
                    "bootloader",
                    "heatpump_model",
                    "heatpump_firmware",
                )
            }
        )

    @property
    def is_empty(self) -> bool:
        return not any(asdict(self).values())

    @property
    def manufactured(self) -> str | None:
        """When the unit was built, read out of its serial number.

        CTC writes a serial as three groups of four digits: the product, the
        year and week it was made, and a sequence number.
        https://ctc.se/blogg/varmepump/guide-for-produktens-serienummer
        """
        if not self.serial:
            return None
        digits = "".join(ch for ch in self.serial if ch.isdigit())
        if len(digits) < 12:
            return None
        year, week = digits[4:6], digits[6:8]
        if not 1 <= int(week) <= 53:
            return None
        return f"20{year} vecka {int(week)}"


def _same_row(one: Widget, other: Widget) -> bool:
    return abs(one.y - other.y) <= _ROW_TOLERANCE


async def _async_english(client: CtcWebClient, screen: int, widget: Widget) -> str | None:
    try:
        return await client.async_english_label(screen, widget)
    except CtcWebError:
        return None


async def _async_read_system_screen(
    client: CtcWebClient, screen: int, into: Identity
) -> bool:
    """Pair the labels on the system screen with the strings beside them."""
    widgets = [w for w in await client.async_widgets(screen) if w.visible]
    strings = [w for w in widgets if w.kind == 5 and w.text_value]
    if not strings:
        return False

    wanted = {
        LABEL_SERIAL: "serial",
        LABEL_MAC: "mac",
        LABEL_PROGRAM: "display_firmware",
        LABEL_BOOTLOADER: "bootloader",
    }
    found = False
    for widget in widgets:
        if widget.label is None or widget.kind == 5:
            continue
        english = await _async_english(client, screen, widget)
        field = wanted.get(english or "")
        if field is None:
            continue
        beside = [s for s in strings if _same_row(s, widget) and s.x > widget.x]
        if not beside:
            continue
        setattr(into, field, min(beside, key=lambda w: w.x).text_value)
        found = True
    return found


async def _async_read_heatpump_screen(
    client: CtcWebClient, screen: int, into: Identity
) -> bool:
    """Read the outdoor unit's model and its control board's software."""
    widgets = [w for w in await client.async_widgets(screen) if w.visible]
    values = await client.async_vars(screen)
    found = False
    for widget in widgets:
        if widget.label is None:
            continue
        english = await _async_english(client, screen, widget)
        if english == LABEL_MODEL:
            # The model is drawn as its own caption to the right of the label,
            # because the panel picks it out of a list rather than printing it.
            beside = [
                w
                for w in widgets
                if w.label and w is not widget and _same_row(w, widget) and w.x > widget.x
            ]
            if beside:
                into.heatpump_model = (min(beside, key=lambda w: w.x).label or "").strip()
                found = True
        elif english == LABEL_HP_SOFTWARE:
            beside = [
                w
                for w in widgets
                if w.value_fmt and w.value_vars and _same_row(w, widget) and w.x > widget.x
            ]
            for candidate in sorted(beside, key=lambda w: w.x):
                index = candidate.value_vars[0]  # type: ignore[index]
                raw = values[index] if index < len(values) else None
                if isinstance(raw, int) and FIRMWARE_MIN <= raw < FIRMWARE_MAX:
                    into.heatpump_firmware = str(raw)
                    found = True
                    break
    return found


async def async_read_identity(client: CtcWebClient) -> Identity:
    """Find and read the unit's identity.

    Candidate screens are shortlisted from their values alone, which is one
    cheap request each, before any screen definition is fetched.
    """
    identity = Identity()
    page_map = await client.async_screen_map()
    screens = sorted({s for members in page_map.values() for s in members})

    system_candidates: list[int] = []
    heatpump_candidates: list[int] = []
    for screen in screens:
        try:
            values = await client.async_vars(screen)
        except CtcWebError:
            continue
        if sum(1 for v in values if isinstance(v, str) and v) >= 3:
            system_candidates.append(screen)
        if any(
            isinstance(v, int) and FIRMWARE_MIN <= v < FIRMWARE_MAX for v in values
        ):
            heatpump_candidates.append(screen)

    for screen in system_candidates:
        try:
            if await _async_read_system_screen(client, screen, identity):
                break
        except CtcWebError as err:
            _LOGGER.debug("System screen %s unreadable: %s", screen, err)

    for screen in heatpump_candidates:
        try:
            if await _async_read_heatpump_screen(client, screen, identity):
                break
        except CtcWebError as err:
            _LOGGER.debug("Heat pump screen %s unreadable: %s", screen, err)

    if identity.is_empty:
        _LOGGER.debug("Could not read any identity from the display")
    return identity
