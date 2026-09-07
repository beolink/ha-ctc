"""Mode controls backed by CTC's volatile 1000 block."""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import CtcConfigEntry
from .const import CONTROL_SELECTS, DOMAIN, ControlRegister
from .modbus_api import CtcModbusError

_LOGGER = logging.getLogger(__name__)

RELEASE = "Släpp styrningen"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CtcConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    if not runtime.control_enabled:
        return
    async_add_entities(
        CtcControlSelect(runtime, register) for register in CONTROL_SELECTS
    )


class CtcControlSelect(SelectEntity):
    """A volatile control register with a fixed set of modes."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, runtime, register: ControlRegister) -> None:
        self._runtime = runtime
        self._register = register
        self._enum = register.enum or {}
        host = next(iter(runtime.device["identifiers"]))[1]
        self._attr_unique_id = f"{DOMAIN}_{host}_{register.key}"
        self._attr_name = register.name
        self._attr_device_info = runtime.device
        self._attr_options = [RELEASE, *self._enum.values()]
        if register.icon:
            self._attr_icon = register.icon

    @property
    def current_option(self) -> str:
        raw = self._runtime.control.get(self._register.address)
        if raw is None:
            return RELEASE
        return self._enum.get(raw, RELEASE)

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        return {
            "register": str(self._register.address),
            "not": "flyktigt register, nollställs av pumpen cirka fem minuter efter sista skrivningen",
        }

    async def async_select_option(self, option: str) -> None:
        if option == RELEASE:
            await self._runtime.control.async_set(self._register.address, None)
            self.async_write_ha_state()
            return
        for raw, label in self._enum.items():
            if label == option:
                try:
                    await self._runtime.control.async_set(self._register.address, raw)
                except CtcModbusError as err:
                    _LOGGER.error("Could not write %s: %s", self._register.name, err)
                    raise
                self.async_write_ha_state()
                return
