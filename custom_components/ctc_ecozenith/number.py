"""Control entities backed by CTC's volatile 1000 block.

Writing here is the safe way to steer the unit. The registers are not stored in
EEPROM, so they can be written as often as wanted, and the controller drops them
about five minutes after the last write. Setting an entity back to its own
"release" position stops the override and hands control back to the heat pump.

The stored settings in the 61500 block are deliberately never written. CTC warns
that the number of write cycles there is limited and that frequent writes can
destroy the controller.
"""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import (
    EntityCategory,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import CtcConfigEntry
from .const import CONTROL_NUMBERS, DOMAIN, ControlRegister
from .modbus_api import CtcModbusError

_LOGGER = logging.getLogger(__name__)

UNITS = {
    "°C": UnitOfTemperature.CELSIUS,
    "kW": UnitOfPower.KILO_WATT,
    "h": UnitOfTime.HOURS,
    "rps": "rps",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CtcConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    if not runtime.control_enabled:
        return
    async_add_entities(
        CtcControlNumber(runtime, register) for register in CONTROL_NUMBERS
    )


class CtcControlNumber(NumberEntity):
    """One volatile control register exposed as a number."""

    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, runtime, register: ControlRegister) -> None:
        self._runtime = runtime
        self._register = register
        host = next(iter(runtime.device["identifiers"]))[1]
        self._attr_unique_id = f"{DOMAIN}_{host}_{register.key}"
        self._attr_name = register.name
        self._attr_device_info = runtime.device
        self._attr_native_min_value = register.minimum
        self._attr_native_max_value = register.maximum
        self._attr_native_step = register.step
        self._attr_native_unit_of_measurement = UNITS.get(
            register.unit or "", register.unit
        )
        if register.icon:
            self._attr_icon = register.icon

    @property
    def native_value(self) -> float | None:
        """Return the override in force, or the unit's own setting when idle."""
        raw = self._runtime.control.get(self._register.address)
        if raw is not None:
            return round(raw * self._register.scale, 3)
        if self._register.mirror is not None:
            for description in self._runtime.modbus.descriptions:
                if description.address == self._register.mirror:
                    value = (self._runtime.modbus.data or {}).get(description.key)
                    if isinstance(value, (int, float)):
                        return value
        return None

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        overriding = self._register.address in self._runtime.control.active
        return {
            "styrning aktiv": "ja" if overriding else "nej",
            "register": str(self._register.address),
            "not": "flyktigt register, nollställs av pumpen cirka fem minuter efter sista skrivningen",
        }

    async def async_set_native_value(self, value: float) -> None:
        raw = int(round(value / self._register.scale))
        try:
            await self._runtime.control.async_set(self._register.address, raw)
        except CtcModbusError as err:
            _LOGGER.error("Could not write %s: %s", self._register.name, err)
            raise
        self.async_write_ha_state()
