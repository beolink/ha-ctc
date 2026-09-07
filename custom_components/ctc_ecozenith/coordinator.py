"""Update coordinators for the two transports.

The Modbus coordinator is the workhorse: it reads the documented registers in
contiguous blocks, it has no side effects and it runs on a short interval.

The web coordinator harvests the values Modbus does not expose. Reading a page
other than the one on the panel means navigating there, which moves the physical
display, so it runs rarely, it checks first whether somebody is using the panel,
and it puts the panel back when it is done.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .catalogue import numeric_value
from .const import (
    CONTROL_KEEPALIVE_SECONDS,
    DOMAIN,
    MODBUS_SENSORS,
    MODBUS_SETTINGS,
    ModbusSensor,
    SlowPage,
)
from .modbus_api import (
    CtcModbusClient,
    CtcModbusError,
    decode_pair,
    decode_signed,
    is_sentinel,
    plan_blocks,
)
from .web_api import CtcWebClient, CtcWebError

_LOGGER = logging.getLogger(__name__)

class CtcModbusCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll the documented Modbus registers."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: CtcModbusClient,
        interval: int,
        include_settings: bool = True,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} modbus",
            update_interval=timedelta(seconds=interval),
        )
        self.client = client
        self.descriptions: tuple[ModbusSensor, ...] = (
            MODBUS_SENSORS + MODBUS_SETTINGS if include_settings else MODBUS_SENSORS
        )
        self._blocks = plan_blocks(self.descriptions)
        self._missing: set[int] = set()

    async def _async_update_data(self) -> dict[str, Any]:
        raw: dict[int, int] = {}
        failures = 0
        for start, count in self._blocks:
            values = await self.client.async_read_one(start, count)
            if values is None:
                failures += 1
                continue
            for offset, value in enumerate(values):
                raw[start + offset] = value
        if not raw:
            raise UpdateFailed("no Modbus register could be read")
        if failures:
            _LOGGER.debug("%s of %s register blocks did not answer", failures, len(self._blocks))

        data: dict[str, Any] = {}
        for description in self.descriptions:
            value = self._decode(description, raw)
            if value is not None:
                data[description.key] = value
        return data

    def _decode(self, description: ModbusSensor, raw: dict[int, int]) -> Any:
        if description.address not in raw:
            return None
        first = raw[description.address]
        if description.count == 2:
            second = raw.get(description.address + 1)
            if second is None:
                return None
            combined = decode_pair(first, second)
            return round(combined * description.scale, 3)
        if is_sentinel(first):
            return None
        value = decode_signed(first) if description.signed else first
        if description.enum is not None:
            return description.enum.get(value, f"Okänd ({value})")
        return round(value * description.scale, 3)


class CtcWebCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Harvest the display's own values for the pages the user selected."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: CtcWebClient,
        pages: list[SlowPage],
        interval: int,
        restore_page: bool = True,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} display",
            update_interval=timedelta(seconds=interval),
        )
        self.client = client
        self.pages = pages
        self.restore_page = restore_page
        self._expected_page: int | None = None
        self.last_skip_reason: str | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        if not self.pages:
            return {}
        data: dict[str, Any] = dict(self.data or {})
        try:
            origin = await self.client.async_current_page()
        except CtcWebError as err:
            raise UpdateFailed(f"could not read the panel state: {err}") from err

        # Somebody standing at the panel would be fighting us for it. If the page
        # is not where we left it, leave it alone this round.
        if (
            self._expected_page is not None
            and origin != self._expected_page
            and len(self.pages) > 1
        ):
            self.last_skip_reason = "panelen används av någon annan"
            _LOGGER.debug("Panel is on page %s, not %s; skipping this cycle", origin, self._expected_page)
            return data
        self.last_skip_reason = None

        restore_to = origin if self.restore_page else None
        try:
            for page in self.pages:
                if await self.client.async_current_page() != page.page:
                    moved = await self.client.async_goto_page(page.page)
                    if not moved:
                        _LOGGER.debug("Could not reach page %s", page.page)
                        continue
                values_by_screen: dict[int, list[Any]] = {}
                for screen in page.screens:
                    try:
                        values_by_screen[screen] = await self.client.async_vars(screen)
                    except CtcWebError as err:
                        _LOGGER.debug("Screen %s unreadable: %s", screen, err)
                for value in page.values:
                    number = numeric_value(value, values_by_screen.get(value.screen, []))
                    if number is not None:
                        data[value.key] = number
        except CtcWebError as err:
            raise UpdateFailed(f"display read failed: {err}") from err
        finally:
            if restore_to is not None:
                try:
                    await self.client.async_goto_page(restore_to)
                except CtcWebError:
                    _LOGGER.debug("Could not restore the panel to page %s", restore_to)
            try:
                self._expected_page = await self.client.async_current_page()
            except CtcWebError:
                self._expected_page = None

        if not data:
            raise UpdateFailed("no value could be read from the display")
        return data


class CtcControlManager:
    """Keep CTC's volatile control registers alive.

    The 1000 block is write only and the controller forgets it roughly five
    minutes after the last write. Rewriting every minute keeps a wide margin, and
    stopping simply hands control back to the heat pump.
    """

    def __init__(self, hass: HomeAssistant, client: CtcModbusClient) -> None:
        self._hass = hass
        self._client = client
        self._values: dict[int, int] = {}
        self._unsub = None

    @property
    def active(self) -> dict[int, int]:
        return dict(self._values)

    def get(self, address: int) -> int | None:
        return self._values.get(address)

    async def async_set(self, address: int, raw: int | None) -> None:
        """Set or release one control register."""
        if raw is None:
            self._values.pop(address, None)
            return
        self._values[address] = raw
        await self._client.async_write(address, raw)
        self._ensure_timer()

    def _ensure_timer(self) -> None:
        if self._unsub is not None or not self._values:
            return
        from homeassistant.helpers.event import async_track_time_interval

        self._unsub = async_track_time_interval(
            self._hass,
            self._async_refresh,
            timedelta(seconds=CONTROL_KEEPALIVE_SECONDS),
        )

    async def _async_refresh(self, _now) -> None:
        for address, raw in list(self._values.items()):
            try:
                await self._client.async_write(address, raw)
            except CtcModbusError as err:
                _LOGGER.warning("Could not refresh control register %s: %s", address, err)

    async def async_stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        self._values.clear()
