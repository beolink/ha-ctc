"""A button that hands the heat pump back to its own settings."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import CtcConfigEntry
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CtcConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    if not runtime.control_enabled:
        return
    async_add_entities([CtcReleaseControl(runtime)])


class CtcReleaseControl(ButtonEntity):
    """Stop every override at once.

    A mode can be released on its own, but a number has no release position, so
    without this a setpoint once changed from Home Assistant stays overridden for
    as long as Home Assistant runs.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:hand-back-left-outline"
    _attr_name = "Släpp all styrning"

    def __init__(self, runtime) -> None:
        self._runtime = runtime
        host = next(iter(runtime.device["identifiers"]))[1]
        self._attr_unique_id = f"{DOMAIN}_{host}_release_control"
        self._attr_device_info = runtime.device

    async def async_press(self) -> None:
        self._runtime.control.async_release_all()
