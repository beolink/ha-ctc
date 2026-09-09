"""Sensors from both transports."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfPressure,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from homeassistant.const import EntityCategory

from . import CtcConfigEntry, current_totals
from .const import DOMAIN, ModbusSensor, SlowValue

DEVICE_CLASSES = {
    "temperature": SensorDeviceClass.TEMPERATURE,
    "power": SensorDeviceClass.POWER,
    "energy": SensorDeviceClass.ENERGY,
    "current": SensorDeviceClass.CURRENT,
    "voltage": SensorDeviceClass.VOLTAGE,
    "pressure": SensorDeviceClass.PRESSURE,
}

UNITS = {
    "°C": UnitOfTemperature.CELSIUS,
    "kW": UnitOfPower.KILO_WATT,
    "kWh": UnitOfEnergy.KILO_WATT_HOUR,
    "A": UnitOfElectricCurrent.AMPERE,
    "V": UnitOfElectricPotential.VOLT,
    "bar": UnitOfPressure.BAR,
    "h": UnitOfTime.HOURS,
    "min": UnitOfTime.MINUTES,
    "l/min": UnitOfVolumeFlowRate.LITERS_PER_MINUTE,
    "%": "%",
    "rps": "rps",
    "ppm": "ppm",
}

# Units that imply what the reading is, used to give display values a device
# class the display itself never states.
UNIT_TO_CLASS = {
    "°C": SensorDeviceClass.TEMPERATURE,
    "kW": SensorDeviceClass.POWER,
    "kWh": SensorDeviceClass.ENERGY,
    "A": SensorDeviceClass.CURRENT,
    "V": SensorDeviceClass.VOLTAGE,
    "bar": SensorDeviceClass.PRESSURE,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CtcConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create every sensor for this heat pump."""
    runtime = entry.runtime_data
    entities: list[SensorEntity] = [
        CtcModbusSensor(runtime, description)
        for description in runtime.modbus.descriptions
    ]
    if runtime.web is not None:
        for page in runtime.pages:
            for value in page.values:
                entities.append(CtcDisplaySensor(runtime, page.title, value))

    # What the unit is: read once from the display and then unchanging.
    for key, name, value, icon in (
        ("hp_model", "Värmepumpsmodell", runtime.identity.heatpump_model, "mdi:heat-pump-outline"),
        ("display_fw", "Programversion display", runtime.identity.display_firmware, "mdi:chip"),
        ("hp_fw", "Programversion VP-styrkort", runtime.identity.heatpump_firmware, "mdi:chip"),
        ("bootloader", "Bootloaderversion", runtime.identity.bootloader, "mdi:chip"),
        ("serial", "Serienummer", runtime.identity.serial, "mdi:identifier"),
        ("made", "Tillverkad", runtime.identity.manufactured, "mdi:factory"),
    ):
        if value:
            entities.append(CtcIdentitySensor(runtime, key, name, value, icon))

    if runtime.cop is not None:
        for span in ("day", "year", "lifetime"):
            entities.append(CtcCopSensor(runtime, span))

    async_add_entities(entities)


class CtcModbusSensor(CoordinatorEntity, SensorEntity):
    """One documented Modbus register."""

    _attr_has_entity_name = True

    def __init__(self, runtime, description: ModbusSensor) -> None:
        super().__init__(runtime.modbus)
        self._description = description
        host = next(iter(runtime.device["identifiers"]))[1]
        self._attr_unique_id = f"{DOMAIN}_{host}_{description.key}"
        self._attr_name = description.name
        self._attr_device_info = runtime.device
        self._attr_entity_registry_enabled_default = description.enabled_default
        if description.icon:
            self._attr_icon = description.icon
        if description.enum is None:
            self._attr_native_unit_of_measurement = UNITS.get(
                description.unit or "", description.unit
            )
            self._attr_device_class = DEVICE_CLASSES.get(description.device_class or "")
            if description.state_class == "total_increasing":
                self._attr_state_class = SensorStateClass.TOTAL_INCREASING
            elif description.state_class == "measurement":
                self._attr_state_class = SensorStateClass.MEASUREMENT
        else:
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = list(description.enum.values())

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get(self._description.key)

    @property
    def available(self) -> bool:
        return (
            self.coordinator.last_update_success
            and self._description.key in (self.coordinator.data or {})
        )


class CtcDisplaySensor(CoordinatorEntity, SensorEntity):
    """A reading harvested from the display's own web interface.

    These come from a page the integration has to navigate to, so they update on
    the slow interval rather than continuously.
    """

    _attr_has_entity_name = True

    def __init__(self, runtime, page_title: str, value: SlowValue) -> None:
        super().__init__(runtime.web)
        self._value = value
        host = next(iter(runtime.device["identifiers"]))[1]
        self._attr_unique_id = f"{DOMAIN}_{host}_{value.key}"
        self._attr_name = f"{page_title}: {value.label}" if page_title else value.label
        self._attr_device_info = runtime.device
        unit = value.unit
        self._attr_native_unit_of_measurement = UNITS.get(unit or "", unit)
        device_class = UNIT_TO_CLASS.get(unit or "")
        if device_class is not None:
            self._attr_device_class = device_class
        if unit:
            self._attr_state_class = SensorStateClass.MEASUREMENT
        # Only a handful of these are interesting to most people; the rest are
        # created but left switched off so the entity list stays usable.
        self._attr_entity_registry_enabled_default = unit in ("kW", "kWh", "°C")

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get(self._value.key)

    @property
    def available(self) -> bool:
        return (
            self.coordinator.last_update_success
            and self._value.key in (self.coordinator.data or {})
        )

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        return {
            "källa": "displayens webbgränssnitt",
            "sida": str(self._value.page),
            "skärm": str(self._value.screen),
        }


class CtcIdentitySensor(SensorEntity):
    """Something the unit says about itself and then never changes.

    Read from the display once at setup and stored with the entry, so it costs
    nothing to keep and survives the display being unreachable.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, runtime, key: str, name: str, value: str, icon: str) -> None:
        host = next(iter(runtime.device["identifiers"]))[1]
        self._attr_unique_id = f"{DOMAIN}_{host}_{key}"
        self._attr_name = name
        self._attr_native_value = value
        self._attr_icon = icon
        self._attr_device_info = runtime.device


class CtcCopSensor(CoordinatorEntity, SensorEntity):
    """Delivered heat against supplied energy.

    Modbus knows what went in but never what came out, so this exists only
    where the display's history page is harvested.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:gauge"
    _attr_suggested_display_precision = 2

    NAMES = {
        "day": "Dygnsvärmefaktor",
        "year": "Årsvärmefaktor",
        "lifetime": "Värmefaktor, hela livslängden",
    }

    def __init__(self, runtime, span: str) -> None:
        super().__init__(runtime.web)
        self._runtime = runtime
        self._span = span
        host = next(iter(runtime.device["identifiers"]))[1]
        self._attr_unique_id = f"{DOMAIN}_{host}_cop_{span}"
        self._attr_name = self.NAMES[span]
        self._attr_device_info = runtime.device

    def _result(self):
        out, consumed = current_totals(self._runtime)
        if self._span == "day":
            return self._runtime.cop.result_day(out, consumed)
        return self._runtime.cop.result(out, consumed)

    @property
    def native_value(self) -> float | None:
        out, consumed = current_totals(self._runtime)
        if self._span == "lifetime":
            if out is None or consumed is None or consumed < 50:
                return None
            return round(out / consumed, 2)
        result = self._result()
        # Reporting one span's figure under another span's name would be a
        # different number wearing the wrong label.
        return result.value if result.basis == self._span else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return self._result().as_attributes()

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self.native_value is not None
