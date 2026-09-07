"""The CTC EcoZenith integration.

Reads a CTC heat pump locally over Modbus TCP, and optionally harvests the extra
values that only the display knows from its own web interface. Nothing goes near
myUplink or any other cloud.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo

from .catalogue import pages_from_storage
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
    PLATFORMS,
    SlowPage,
)
from .coordinator import CtcControlManager, CtcModbusCoordinator, CtcWebCoordinator
from .modbus_api import CtcModbusClient
from .web_api import CtcWebClient

_LOGGER = logging.getLogger(__name__)


@dataclass
class CtcRuntime:
    """Everything one config entry needs at runtime."""

    modbus: CtcModbusCoordinator
    control: CtcControlManager
    device: DeviceInfo
    web: CtcWebCoordinator | None = None
    pages: list[SlowPage] = field(default_factory=list)
    control_enabled: bool = False


type CtcConfigEntry = ConfigEntry[CtcRuntime]


async def async_setup_entry(hass: HomeAssistant, entry: CtcConfigEntry) -> bool:
    """Set up one heat pump."""
    host = entry.data[CONF_HOST]
    modbus_port = entry.data.get(CONF_MODBUS_PORT, DEFAULT_MODBUS_PORT)
    web_port = entry.data.get(CONF_WEB_PORT, DEFAULT_WEB_PORT)
    slave = entry.data.get(CONF_SLAVE, DEFAULT_SLAVE)
    options = entry.options

    modbus_client = CtcModbusClient(host, modbus_port, slave)
    modbus = CtcModbusCoordinator(
        hass,
        modbus_client,
        int(options.get(CONF_FAST_INTERVAL, DEFAULT_FAST_INTERVAL)),
    )
    await modbus.async_config_entry_first_refresh()

    device = DeviceInfo(
        identifiers={(DOMAIN, host)},
        manufacturer="CTC / Enertech",
        model=entry.data.get("model", "CTC"),
        # The device name becomes the prefix of every entity id, so it stays
        # short. The entry title keeps the address for telling two units apart.
        name=f"CTC {entry.data.get('model', 'värmepump')}",
        configuration_url=f"http://{host}:{web_port}/main.html",
    )

    runtime = CtcRuntime(
        modbus=modbus,
        control=CtcControlManager(hass, modbus_client),
        device=device,
        control_enabled=bool(options.get(CONF_ENABLE_CONTROL, False)),
    )

    pages = pages_from_storage(options.get(CONF_SLOW_PAGES, []))
    if pages:
        web_client = CtcWebClient(
            async_get_clientsession(hass),
            host,
            web_port,
            int(options.get(CONF_LANGUAGE, LANG_SWEDISH)),
        )
        web = CtcWebCoordinator(
            hass,
            web_client,
            pages,
            int(options.get(CONF_SLOW_INTERVAL, DEFAULT_SLOW_INTERVAL)),
            restore_page=bool(options.get(CONF_RESTORE_PAGE, True)),
        )
        # A failure here must not take the whole entry down: Modbus is the base
        # and the display is a supplement.
        await web.async_refresh()
        runtime.web = web
        runtime.pages = pages

    entry.runtime_data = runtime
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: CtcConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: CtcConfigEntry) -> bool:
    """Tear one heat pump down, releasing the single Modbus slot."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        runtime = entry.runtime_data
        await runtime.control.async_stop()
        await runtime.modbus.client.async_close()
    return unloaded
