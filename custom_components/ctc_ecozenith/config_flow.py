"""Config and options flow for CTC EcoZenith.

Setup has two questions. First where the unit is, answered by scanning the local
network and falling back to typing an address. Then which of the display's own
pages should be harvested for the values Modbus does not carry, offered as a
list of tick boxes built from the unit's own menu.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from .catalogue import async_discover_pages, pages_from_storage, pages_to_storage
from .const import (
    CONF_ENABLE_CONTROL,
    CONF_FAST_INTERVAL,
    CONF_LANGUAGE,
    CONF_MODBUS_PORT,
    CONF_RESTORE_PAGE,
    CONF_SLAVE,
    CONF_SLOW_INTERVAL,
    CONF_SLOW_PAGES,
    CONF_WEB_PORT,
    DEFAULT_FAST_INTERVAL,
    DEFAULT_MODBUS_PORT,
    DEFAULT_SLAVE,
    DEFAULT_SLOW_INTERVAL,
    DEFAULT_WEB_PORT,
    DOMAIN,
    LANG_SWEDISH,
    MIN_SLOW_INTERVAL,
)
from .discovery import DiscoveredDisplay, async_discover, async_probe_host
from .modbus_api import CtcModbusClient, CtcModbusError
from .web_api import CtcWebClient, CtcWebError

_LOGGER = logging.getLogger(__name__)

CONF_PICKED = "picked"
MANUAL = "manual"


class CtcConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Guide the user from an empty form to a working entry."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._modbus_port = DEFAULT_MODBUS_PORT
        self._web_port = DEFAULT_WEB_PORT
        self._slave = DEFAULT_SLAVE
        self._model: str = "CTC"
        self._settings_name: str = ""
        self._found: list[DiscoveredDisplay] = []
        self._pages: list[Any] = []

    # ------------------------------------------------------------ entry point

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Scan the network, then let the user pick or type an address."""
        if user_input is not None:
            picked = user_input[CONF_PICKED]
            if picked == MANUAL:
                return await self.async_step_manual()
            self._host = picked
            for display in self._found:
                if display.host == picked:
                    self._model = display.model
                    self._settings_name = display.settings_name
            return await self.async_step_connect()

        session = async_get_clientsession(self.hass)
        try:
            self._found = await async_discover(session)
        except Exception as err:  # noqa: BLE001 - a failed sweep must not block setup
            _LOGGER.debug("Network sweep failed: %s", err)
            self._found = []

        if not self._found:
            return await self.async_step_manual()

        options = [
            selector.SelectOptionDict(value=display.host, label=display.label)
            for display in self._found
        ]
        options.append(
            selector.SelectOptionDict(value=MANUAL, label="Ange IP-adress manuellt")
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_PICKED, default=self._found[0].host): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options, mode=selector.SelectSelectorMode.LIST
                    )
                )
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Ask for the address by hand when the sweep found nothing."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._host = user_input[CONF_HOST].strip()
            self._modbus_port = user_input[CONF_MODBUS_PORT]
            self._web_port = user_input[CONF_WEB_PORT]
            self._slave = user_input[CONF_SLAVE]
            session = async_get_clientsession(self.hass)
            display = await async_probe_host(session, self._host, self._web_port)
            if display is None:
                errors["base"] = "not_a_ctc"
            else:
                self._model = display.model
                self._settings_name = display.settings_name
                return await self.async_step_connect()

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=self._host or ""): str,
                vol.Required(CONF_MODBUS_PORT, default=self._modbus_port): int,
                vol.Required(CONF_WEB_PORT, default=self._web_port): int,
                vol.Required(CONF_SLAVE, default=self._slave): int,
            }
        )
        return self.async_show_form(
            step_id="manual", data_schema=schema, errors=errors
        )

    # ------------------------------------------------------------- validation

    async def async_step_connect(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Check that Modbus answers before going any further."""
        assert self._host is not None
        await self.async_set_unique_id(f"{DOMAIN}_{self._host}")
        self._abort_if_unique_id_configured()

        client = CtcModbusClient(self._host, self._modbus_port, self._slave)
        try:
            await client.async_probe()
        except CtcModbusError as err:
            _LOGGER.debug("Modbus probe failed: %s", err)
            await client.async_close()
            return self.async_show_form(
                step_id="manual",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_HOST, default=self._host): str,
                        vol.Required(CONF_MODBUS_PORT, default=self._modbus_port): int,
                        vol.Required(CONF_WEB_PORT, default=self._web_port): int,
                        vol.Required(CONF_SLAVE, default=self._slave): int,
                    }
                ),
                errors={"base": "modbus_failed"},
                description_placeholders={"host": self._host},
            )
        finally:
            await client.async_close()

        return await self.async_step_slow()

    # ------------------------------------------------------------ slow values

    async def async_step_slow(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Offer the display's own pages as tick boxes."""
        assert self._host is not None

        if user_input is not None:
            chosen = {int(page) for page in user_input.get(CONF_SLOW_PAGES, [])}
            keep = [page for page in self._pages if page.page in chosen]
            return self.async_create_entry(
                title=f"{self._model} ({self._host})",
                data={
                    CONF_HOST: self._host,
                    CONF_MODBUS_PORT: self._modbus_port,
                    CONF_WEB_PORT: self._web_port,
                    CONF_SLAVE: self._slave,
                    "model": self._model,
                    "settings_name": self._settings_name,
                },
                options={
                    CONF_SLOW_PAGES: pages_to_storage(keep),
                    CONF_SLOW_INTERVAL: int(
                        user_input.get(CONF_SLOW_INTERVAL, DEFAULT_SLOW_INTERVAL)
                    ),
                    CONF_FAST_INTERVAL: DEFAULT_FAST_INTERVAL,
                    CONF_RESTORE_PAGE: user_input.get(CONF_RESTORE_PAGE, True),
                    CONF_ENABLE_CONTROL: False,
                    CONF_LANGUAGE: LANG_SWEDISH,
                },
            )

        session = async_get_clientsession(self.hass)
        client = CtcWebClient(session, self._host, self._web_port, LANG_SWEDISH)
        try:
            self._pages = await async_discover_pages(client)
        except CtcWebError as err:
            _LOGGER.warning("Could not read the display's menu: %s", err)
            self._pages = []

        if not self._pages:
            # Modbus alone is a perfectly good entry; the display is a bonus.
            return self.async_create_entry(
                title=f"{self._model} ({self._host})",
                data={
                    CONF_HOST: self._host,
                    CONF_MODBUS_PORT: self._modbus_port,
                    CONF_WEB_PORT: self._web_port,
                    CONF_SLAVE: self._slave,
                    "model": self._model,
                    "settings_name": self._settings_name,
                },
                options={
                    CONF_SLOW_PAGES: [],
                    CONF_SLOW_INTERVAL: DEFAULT_SLOW_INTERVAL,
                    CONF_FAST_INTERVAL: DEFAULT_FAST_INTERVAL,
                    CONF_RESTORE_PAGE: True,
                    CONF_ENABLE_CONTROL: False,
                    CONF_LANGUAGE: LANG_SWEDISH,
                },
            )

        return self.async_show_form(
            step_id="slow",
            data_schema=_slow_schema(self._pages, [], DEFAULT_SLOW_INTERVAL, True),
            description_placeholders={
                "model": self._model,
                "count": str(len(self._pages)),
            },
        )

    # -------------------------------------------------------------- discovery

    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo) -> FlowResult:
        """Offer setup when a device with CTC's MAC prefix appears."""
        host = discovery_info.ip
        await self.async_set_unique_id(f"{DOMAIN}_{host}")
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})

        session = async_get_clientsession(self.hass)
        display = await async_probe_host(session, host, DEFAULT_WEB_PORT)
        if display is None:
            return self.async_abort(reason="not_a_ctc")

        self._host = host
        self._model = display.model
        self._settings_name = display.settings_name
        self.context["title_placeholders"] = {"name": display.label}
        return await self.async_step_connect()

    @staticmethod
    @callback
    def async_get_options_flow(
        entry: config_entries.ConfigEntry,
    ) -> CtcOptionsFlow:
        return CtcOptionsFlow(entry)


def _slow_schema(
    pages: list[Any],
    selected: list[int],
    interval: int,
    restore: bool,
) -> vol.Schema:
    options = [
        selector.SelectOptionDict(
            value=str(page.page),
            label=f"{page.title} ({len(page.values)} värden)",
        )
        for page in pages
    ]
    return vol.Schema(
        {
            vol.Optional(
                CONF_SLOW_PAGES, default=[str(page) for page in selected]
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=options,
                    multiple=True,
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
            vol.Optional(CONF_SLOW_INTERVAL, default=interval): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_SLOW_INTERVAL,
                    max=21600,
                    step=60,
                    unit_of_measurement="s",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(CONF_RESTORE_PAGE, default=restore): bool,
        }
    )


class CtcOptionsFlow(config_entries.OptionsFlow):
    """Change which pages are harvested, how often, and whether control is on."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry
        self._pages: list[Any] = []

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            if user_input.get("rescan"):
                return await self.async_step_rescan()
            return self.async_create_entry(
                title="",
                data={
                    **self._entry.options,
                    CONF_FAST_INTERVAL: int(user_input[CONF_FAST_INTERVAL]),
                    CONF_SLOW_INTERVAL: int(user_input[CONF_SLOW_INTERVAL]),
                    CONF_RESTORE_PAGE: user_input[CONF_RESTORE_PAGE],
                    CONF_ENABLE_CONTROL: user_input[CONF_ENABLE_CONTROL],
                },
            )

        options = self._entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_FAST_INTERVAL,
                    default=options.get(CONF_FAST_INTERVAL, DEFAULT_FAST_INTERVAL),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=10, max=600, step=5, unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_SLOW_INTERVAL,
                    default=options.get(CONF_SLOW_INTERVAL, DEFAULT_SLOW_INTERVAL),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_SLOW_INTERVAL, max=21600, step=60,
                        unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_RESTORE_PAGE, default=options.get(CONF_RESTORE_PAGE, True)
                ): bool,
                vol.Optional(
                    CONF_ENABLE_CONTROL,
                    default=options.get(CONF_ENABLE_CONTROL, False),
                ): bool,
                vol.Optional("rescan", default=False): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_rescan(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Walk the display's menu again and re-offer the tick boxes."""
        if user_input is not None:
            chosen = {int(page) for page in user_input.get(CONF_SLOW_PAGES, [])}
            keep = [page for page in self._pages if page.page in chosen]
            return self.async_create_entry(
                title="",
                data={
                    **self._entry.options,
                    CONF_SLOW_PAGES: pages_to_storage(keep),
                    CONF_SLOW_INTERVAL: int(
                        user_input.get(CONF_SLOW_INTERVAL, DEFAULT_SLOW_INTERVAL)
                    ),
                    CONF_RESTORE_PAGE: user_input.get(CONF_RESTORE_PAGE, True),
                },
            )

        session = async_get_clientsession(self.hass)
        client = CtcWebClient(
            session,
            self._entry.data[CONF_HOST],
            self._entry.data.get(CONF_WEB_PORT, DEFAULT_WEB_PORT),
            self._entry.options.get(CONF_LANGUAGE, LANG_SWEDISH),
        )
        try:
            self._pages = await async_discover_pages(client)
        except CtcWebError as err:
            _LOGGER.warning("Could not read the display's menu: %s", err)
            self._pages = pages_from_storage(
                self._entry.options.get(CONF_SLOW_PAGES, [])
            )

        already = [
            page.page for page in pages_from_storage(self._entry.options.get(CONF_SLOW_PAGES, []))
        ]
        return self.async_show_form(
            step_id="rescan",
            data_schema=_slow_schema(
                self._pages,
                already,
                int(self._entry.options.get(CONF_SLOW_INTERVAL, DEFAULT_SLOW_INTERVAL)),
                bool(self._entry.options.get(CONF_RESTORE_PAGE, True)),
            ),
            description_placeholders={"count": str(len(self._pages))},
        )
