"""Binary sensors derived from the Modbus status registers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import CtcConfigEntry
from .const import DOMAIN


@dataclass(frozen=True)
class DerivedBinary:
    """A boolean worked out from one of the decoded Modbus values."""

    key: str
    name: str
    source: str
    test: Callable[[object], bool]
    device_class: BinarySensorDeviceClass | None = None
    icon: str | None = None


def _running(value: object) -> bool:
    return isinstance(value, str) and value in (
        "Till värme",
        "Till kyla",
        "Till varmvatten",
    )


DERIVED: tuple[DerivedBinary, ...] = (
    DerivedBinary(
        "compressor_running",
        "Kompressor i drift",
        "hp1_status",
        _running,
        BinarySensorDeviceClass.RUNNING,
        "mdi:heat-pump",
    ),
    DerivedBinary(
        "defrosting",
        "Avfrostning",
        "hp1_status",
        lambda v: v == "Avfrostning",
        None,
        "mdi:snowflake-melt",
    ),
    DerivedBinary(
        "alarm",
        "Larm",
        "hp1_status",
        lambda v: v == "Av, larm",
        BinarySensorDeviceClass.PROBLEM,
    ),
    DerivedBinary(
        "blocked",
        "Blockerad",
        "hp1_status",
        lambda v: v == "Av, blockerad",
        None,
        "mdi:cancel",
    ),
    DerivedBinary(
        "immersion_active",
        "Elpatron aktiv",
        "immersion_lower_kw",
        lambda v: isinstance(v, (int, float)) and v > 0,
        BinarySensorDeviceClass.RUNNING,
        "mdi:heating-coil",
    ),
    DerivedBinary(
        "smartgrid_active",
        "SmartGrid aktiv",
        "sg_mode",
        lambda v: isinstance(v, str) and v != "Normal",
        None,
        "mdi:transmission-tower",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CtcConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    async_add_entities(CtcDerivedBinary(runtime, item) for item in DERIVED)


class CtcDerivedBinary(CoordinatorEntity, BinarySensorEntity):
    """A boolean read off one of the status registers."""

    _attr_has_entity_name = True

    def __init__(self, runtime, item: DerivedBinary) -> None:
        super().__init__(runtime.modbus)
        self._item = item
        host = next(iter(runtime.device["identifiers"]))[1]
        self._attr_unique_id = f"{DOMAIN}_{host}_{item.key}"
        self._attr_name = item.name
        self._attr_device_info = runtime.device
        if item.device_class:
            self._attr_device_class = item.device_class
        if item.icon:
            self._attr_icon = item.icon

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data or {}
        if self._item.source not in data:
            return None
        return self._item.test(data[self._item.source])

    @property
    def available(self) -> bool:
        return (
            self.coordinator.last_update_success
            and self._item.source in (self.coordinator.data or {})
        )
