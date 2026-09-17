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
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .catalogue import numeric_value
from .const import (
    HARVEST_PATIENCE,
    RETRY_INTERVAL,
    CONTROL_KEEPALIVE_SECONDS,
    DOMAIN,
    MODBUS_SENSORS,
    MODBUS_SETTINGS,
    ModbusSensor,
    SlowPage,
)
from .patience import Patience
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
        # Cumulative count of register blocks that did not answer. Only used by
        # the optional daily report, which sends the delta since it last ran.
        self.read_failures = 0

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
        self.read_failures += failures
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
        home_page: int | None = None,
        on_home_page_found: Any = None,
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
        #: The page the panel was showing before this integration first touched
        #: it. Kept across restarts so that one failed restore cannot make the
        #: wrong page the new normal.
        self.home_page = home_page
        self._on_home_page_found = on_home_page_found
        self._expected_page: int | None = None
        self.last_skip_reason: str | None = None
        #: When each value was last actually read off the panel. The data keeps
        #: a value through a skipped cycle, so this is the only way to tell a
        #: fresh reading from a carried one.
        self.read_at: dict[str, datetime] = {}
        #: A display that is merely slow should not take every reading with it.
        self.patience = Patience(interval, RETRY_INTERVAL, HARVEST_PATIENCE)

    async def _async_update_data(self) -> dict[str, Any]:
        if not self.pages:
            return {}
        async with self.client.panel:
            try:
                data = await self._async_harvest()
            except UpdateFailed as err:
                shown = self.patience.failed(bool(self.data))
                self.update_interval = timedelta(seconds=self.patience.seconds)
                if shown:
                    raise
                _LOGGER.debug("Display harvest failed (%s), keeping what we have: %s",
                              self.patience.failures, err)
                return dict(self.data or {})
        self.patience.worked()
        self.update_interval = timedelta(seconds=self.patience.seconds)
        return data

    async def _async_harvest(self) -> dict[str, Any]:
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

        if self.home_page is None and origin not in {p.page for p in self.pages}:
            # First time in: whatever the panel was showing is where it belongs.
            self.home_page = origin
            if self._on_home_page_found is not None:
                self._on_home_page_found(origin)

        restore_to = None
        if self.restore_page:
            # Restoring to the page this cycle started on is right until a
            # restore fails, after which that wrong page would become the new
            # reference. The remembered home page breaks that loop.
            restore_to = origin
            if origin in {p.page for p in self.pages} and self.home_page is not None:
                restore_to = self.home_page
        try:
            for page in self.pages:
                if await self.client.async_current_page() != page.page:
                    # The route was recorded during setup. Replaying it is the
                    # only reliable way in, since the menu layout differs between
                    # models and cannot be derived at poll time.
                    moved = await self.client.async_goto_page(page.page, page.route)
                    if not moved:
                        _LOGGER.debug("Could not reach page %s", page.page)
                        continue
                values_by_screen: dict[int, list[Any]] = {}
                for screen in page.screens:
                    try:
                        values_by_screen[screen] = await self.client.async_vars(screen)
                    except CtcWebError as err:
                        _LOGGER.debug("Screen %s unreadable: %s", screen, err)
                read_at = datetime.now(timezone.utc)
                for value in page.values:
                    number = numeric_value(value, values_by_screen.get(value.screen, []))
                    if number is not None:
                        data[value.key] = number
                        self.read_at[value.key] = read_at
        except CtcWebError as err:
            raise UpdateFailed(f"display read failed: {err}") from err
        finally:
            if restore_to is not None:
                try:
                    await self._async_restore(restore_to)
                except CtcWebError:
                    _LOGGER.debug("Could not restore the panel to page %s", restore_to)
            try:
                self._expected_page = await self.client.async_current_page()
            except CtcWebError:
                self._expected_page = None

        if not data:
            raise UpdateFailed("no value could be read from the display")
        return data

    async def async_restore_page(self, target: int) -> bool:
        """Put the panel back on ``target``, for callers outside the harvest.

        The caller holds ``client.panel``: this is handed to the walk that reads
        the system information page, which takes the lock for its whole trip.
        The lock is not reentrant, so taking it here would deadlock.
        """
        return await self._async_restore(target)

    async def _async_restore(self, target: int) -> bool:
        """Put the panel back, trying every way in that is known.

        A recorded route is the surest, stepping back works inside a submenu,
        and the home screen is the last resort so the panel is at least left
        somewhere sensible rather than deep in a menu.
        """
        route = next((p.route for p in self.pages if p.page == target and p.route), None)
        if await self.client.async_goto_page(target, route):
            return True
        if await self.client.async_step_back_to(target):
            return True
        if self.home_page is not None and target != self.home_page:
            home_route = next(
                (p.route for p in self.pages if p.page == self.home_page and p.route), None
            )
            if await self.client.async_goto_page(self.home_page, home_route):
                return True
        await self.client.async_goto_home()
        return await self.client.async_current_page() == target


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
        self._listeners: list[Callable[[], None]] = []

    @property
    def active(self) -> dict[int, int]:
        return dict(self._values)

    def get(self, address: int) -> int | None:
        return self._values.get(address)

    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call back whenever an override is set or released; returns the undo."""
        self._listeners.append(listener)

        def _remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    async def async_set(self, address: int, raw: int | None) -> None:
        """Set or release one control register."""
        if raw is None:
            self._values.pop(address, None)
            self._notify()
            return
        self._values[address] = raw
        await self._client.async_write(address, raw)
        self._ensure_timer()
        self._notify()

    def async_release_all(self) -> None:
        """Stop overriding anything and hand the unit back to its own settings.

        Nothing is written: the controller forgets an override about five
        minutes after the last write, so it is enough to stop writing. A number
        has no release position of its own, which makes this the only way back
        from one short of restarting Home Assistant.
        """
        self._values.clear()
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        self._notify()

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
