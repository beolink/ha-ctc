"""Read what the unit is: model, serial and the firmware in each board.

None of this comes from Modbus. The display knows it and writes it into two of
its own screens, and unlike the readings, these values are static: they are
written once and stay put, so they can be read whatever page the panel happens
to be showing. That matters, because it means the identity can be read without
walking the panel through its menus, but also that a screen nobody has opened
holds nothing: the display fills the buffer while the screen is shown. Where the
system information page has never been visited, :func:`async_read_identity_via_panel`
walks the panel there once, under guard, and reads it while it is up.

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
from typing import Any, Awaitable, Callable

from .const import NAV_ALLOWED_EN, SYSTEM_INFO_LABEL_EN
from .web_api import CtcWebClient, CtcWebError, Widget, tap_target

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
    def is_complete(self) -> bool:
        """True once every field the display can offer has been seen."""
        return all(asdict(self).values())

    def merged_with(self, newer: "Identity") -> "Identity":
        """Take what the newer read found, and keep the rest.

        A value that was actually read wins, because firmware gets updated and
        the newer read is the fresher one. An empty value does not: the display
        only writes these into a screen once that screen has been shown, so a
        read before that finds nothing, and nothing must not wipe a known value.
        """
        mine = asdict(self)
        theirs = asdict(newer)
        return Identity(**{k: theirs[k] or mine[k] for k in mine})

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


# --------------------------------------------------------- the guarded walk


async def _async_controls(
    client: CtcWebClient, page: int
) -> list[tuple[str, list[int], tuple[int, int]]]:
    """The controls on ``page`` that the walk is allowed to press.

    Everything is matched on the English label, which is the same across models,
    and anything not named in the allow list is not even considered.
    """
    page_map = await client.async_screen_map()
    screens = page_map.get(page, [])
    found: list[tuple[str, list[int], tuple[int, int]]] = []
    for screen in screens:
        try:
            widgets = await client.async_widgets(screen)
        except CtcWebError:
            continue
        for widget in widgets:
            if not widget.visible or not widget.label or widget.width <= 0:
                continue
            english = await _async_english(client, screen, widget)
            if english in NAV_ALLOWED_EN:
                found.append((english, screens, tap_target(widgets, widget)))
    order = {label: n for n, label in enumerate(NAV_ALLOWED_EN)}
    # The page we are after is tried before anything is opened.
    found.sort(key=lambda c: (c[0] != SYSTEM_INFO_LABEL_EN, order.get(c[0], 99)))
    return found


async def _async_descend(
    client: CtcWebClient, page: int, depth: int, visited: set[int]
) -> bool:
    """Look for the system information page from ``page``, one tap at a time.

    Every tap is followed by a check of where the panel actually went. A tap
    that changes nothing is treated as a surprise rather than as something to
    try again: it may have opened a dialog, and the menu next door holds a
    function test, a compressor quick start, reinstallation and a firmware
    update. The walk then stops and the caller puts the panel back.
    """
    if depth <= 0 or page in visited:
        return False
    visited.add(page)
    page_map = await client.async_screen_map()
    for english, screens, (x, y) in await _async_controls(client, page):
        if await client.async_current_page() != page:
            return False  # somebody else is using the panel
        await client.async_click(screens, x, y)
        landed = await client.async_current_page()
        if landed == page:
            _LOGGER.debug("Tapping %s on page %s changed nothing; stopping", english, page)
            return False
        if english == SYSTEM_INFO_LABEL_EN:
            return True
        if await _async_descend(client, landed, depth - 1, visited):
            return True
        await client.async_click(page_map.get(landed, []), 440, 23)
        if await client.async_current_page() != page:
            _LOGGER.debug("Stepping back from page %s did not return to %s", landed, page)
            return False
    return False


async def _async_put_back(client: CtcWebClient, origin: int) -> None:
    """Leave the panel where it was, or at least at home."""
    try:
        if await client.async_current_page() == origin:
            return
        if await client.async_goto_page(origin):
            return
        await client.async_goto_home()
    except CtcWebError as err:
        _LOGGER.debug("Could not put the panel back on page %s: %s", origin, err)


async def async_read_identity_via_panel(
    client: CtcWebClient,
    depth: int = 3,
    restore: "Callable[[int], Awaitable[Any]] | None" = None,
) -> Identity:
    """Walk the panel to the system information page, read it, and go back.

    Only used when the identity is still incomplete, since the values never
    change once they have been read. Returns whatever was found, which is
    nothing at all if the page could not be reached safely.

    Stepping back only climbs the menu the walk came down, so a page on another
    branch is out of reach that way and the panel is left at home instead. The
    harvester knows the recorded routes, so it can put the panel back properly:
    pass its restore as ``restore``.
    """
    identity = Identity()
    try:
        origin = await client.async_current_page()
    except CtcWebError as err:
        _LOGGER.debug("Could not read the panel's page: %s", err)
        return identity
    try:
        home = await client.async_goto_home()
        if home is None:
            _LOGGER.debug("Could not find the home screen; the panel is left alone")
            return identity
        if not await _async_descend(client, home, depth, set()):
            return identity
        page = await client.async_current_page()
        for screen in (await client.async_screen_map()).get(page, []):
            try:
                if await _async_read_system_screen(client, screen, identity):
                    break
            except CtcWebError as err:
                _LOGGER.debug("System screen %s unreadable: %s", screen, err)
    except CtcWebError as err:
        _LOGGER.debug("The walk to system information stopped: %s", err)
    finally:
        if restore is not None:
            try:
                await restore(origin)
            except Exception as err:  # noqa: BLE001 - the panel matters more
                _LOGGER.debug("Restoring page %s failed: %s", origin, err)
                await _async_put_back(client, origin)
        else:
            await _async_put_back(client, origin)
    return identity
