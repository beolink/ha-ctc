"""Constants and register map for the CTC EcoZenith integration.

Two transports are used side by side:

* Modbus TCP on port 502 is the primary source. It is documented by CTC in the
  BMS manual (162 600 16), it is side effect free and it always returns current
  values. It is also the only safe way to control the unit.
* The display's own web server on port 80 ("screen mirror", CTC Remote) is used
  for the values that Modbus does not expose at all, for example delivered heat
  in kW. Only the page the panel is currently showing is kept up to date there,
  so that transport is polled slowly and deliberately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

DOMAIN: Final = "ctc_ecozenith"

DEFAULT_MODBUS_PORT: Final = 502
DEFAULT_WEB_PORT: Final = 80
DEFAULT_SLAVE: Final = 1

CONF_MODBUS_PORT: Final = "modbus_port"
CONF_WEB_PORT: Final = "web_port"
CONF_SLAVE: Final = "slave"
CONF_LANGUAGE: Final = "language"
CONF_SLOW_PAGES: Final = "slow_pages"
CONF_SLOW_INTERVAL: Final = "slow_interval"
CONF_PARK_PAGE: Final = "park_page"
CONF_RESTORE_PAGE: Final = "restore_page"
CONF_ENABLE_CONTROL: Final = "enable_control"
CONF_FAST_INTERVAL: Final = "fast_interval"

DEFAULT_FAST_INTERVAL: Final = 30
DEFAULT_SLOW_INTERVAL: Final = 1800
MIN_SLOW_INTERVAL: Final = 300

# The display speaks many languages. Index 0 is always English, which is what we
# match menu titles against, and index 1 is Swedish.
LANG_ENGLISH: Final = 0
LANG_SWEDISH: Final = 1

# English label of the "Driftinfo" tile on the home screen. The text id differs
# between models (532 on an i255, 570 on an i550 Pro) so the string is matched
# instead of the id.
OPERATION_DATA_LABEL_EN: Final = "Operation data"

# CTC uses these raw values to say "no sensor fitted". They must never reach a
# sensor as a real reading.
SENTINELS: Final = frozenset({9999, -9999, 10000, -10000, 32767, -32768, 4294967295})

# The display's web server drops connections above roughly five in flight.
WEB_MAX_CONCURRENCY: Final = 3

PLATFORMS: Final = ["sensor", "binary_sensor", "number", "select"]


@dataclass(frozen=True)
class ModbusSensor:
    """One readable Modbus holding register."""

    key: str
    address: int
    name: str
    scale: float = 1.0
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = "measurement"
    signed: bool = True
    count: int = 1
    enum: dict[int, str] | None = None
    enabled_default: bool = True
    icon: str | None = None


STATUS_SYSTEM: Final = {
    0: "Värmepump övre",
    1: "Värmepump nedre",
    2: "Spetsvärme",
    3: "Värmepump och spets",
    4: "Värme",
    5: "Varmvatten",
    6: "Pool",
    7: "Kyla",
    8: "Av",
}

STATUS_HEATPUMP: Final = {
    0: "Kompressor av, startfördröjning",
    1: "Redo för start",
    2: "Väntar på flöde",
    3: "Till värme",
    4: "Avfrostning",
    5: "Till kyla",
    6: "Av, blockerad",
    7: "Av, larm",
    8: "Funktionstest",
    30: "Ej definierad",
    31: "Ej tillgänglig",
    32: "Kommunikationsfel",
    33: "Till varmvatten",
}

STATUS_HEATING_SYSTEM: Final = {
    0: "Värme av",
    1: "Semester",
    2: "Nattsänkning",
    3: "Till",
}

DHW_MODE: Final = {0: "Ekonomi", 1: "Normal", 2: "Komfort", 3: "Manuell"}
HEATING_MODE: Final = {0: "Auto", 1: "Till", 2: "Från"}
SG_MODE: Final = {0: "Normal", 1: "Blockering", 2: "Lågpris", 3: "Överkapacitet"}
PRICE_MODE: Final = {1: "Låg", 2: "Normal", 3: "Hög"}
ZONE_MODE: Final = {0: "Av", 1: "Värme", 2: "Kyla", 3: "Auto", 4: "På"}

_T = "temperature"
_P = "power"
_E = "energy"


# Read only measurements, the 62000 block.
MODBUS_SENSORS: Final[tuple[ModbusSensor, ...]] = (
    ModbusSensor("outdoor_temp", 62000, "Utetemperatur", 0.1, "°C", _T),
    ModbusSensor("dhw_stop_temp", 62001, "Stopptemperatur varmvatten", 0.1, "°C", _T, enabled_default=False),
    # 62003 is documented as the hot water temperature but reads a constant 0 on
    # an i550 Pro, where 62276 is the live one. Off by default.
    ModbusSensor("dhw_temp_raw", 62003, "Varmvattentemperatur (62003)", 0.1, "°C", _T, enabled_default=False),
    ModbusSensor("system_status", 62005, "Systemstatus", 1, None, None, None, enum=STATUS_SYSTEM),
    ModbusSensor("radiator_temp", 62006, "Radiatorvatten", 0.1, "°C", _T, enabled_default=False),
    ModbusSensor("hs1_flow_setpoint", 62007, "Framledning börvärde VS1", 0.1, "°C", _T),
    ModbusSensor("hs1_flow", 62011, "Framledning VS1", 0.1, "°C", _T),
    ModbusSensor("return_temp", 62015, "Returtemperatur", 0.1, "°C", _T),
    ModbusSensor("dhw_circulation", 62016, "Varmvattencirkulation", 1, None, None, None, enabled_default=False),
    ModbusSensor("hp1_status", 62017, "Värmepump status", 1, None, None, None, enum=STATUS_HEATPUMP),
    ModbusSensor("hp1_in", 62027, "Värmepump in", 0.1, "°C", _T),
    ModbusSensor("hp1_out", 62037, "Värmepump ut", 0.1, "°C", _T),
    ModbusSensor("hp1_discharge", 62047, "Hetgas", 0.1, "°C", _T),
    ModbusSensor("hp1_suction", 62057, "Suggas", 0.1, "°C", _T),
    ModbusSensor("hp1_high_pressure", 62067, "Högtryck", 0.1, "bar", "pressure"),
    ModbusSensor("hp1_low_pressure", 62077, "Lågtryck", 0.1, "bar", "pressure"),
    ModbusSensor("hp1_brine_in", 62087, "Köldbärare in", 0.1, "°C", _T),
    ModbusSensor("hp1_brine_out", 62097, "Köldbärare ut", 0.1, "°C", _T),
    ModbusSensor("hp1_charge_pump", 62107, "Laddpump", 1, "%", None),
    ModbusSensor("hp1_brine_pump", 62117, "Brinepump", 1, "%", None),
    ModbusSensor("hp1_fan", 62127, "Fläkt", 1, "%", None),
    ModbusSensor("hp1_defrost_timer", 62137, "Avfrostningstimer", 1, None, None, enabled_default=False),
    ModbusSensor("hp1_outdoor_temp", 62147, "Utetemperatur vid värmepump", 0.1, "°C", _T, enabled_default=False),
    ModbusSensor("degree_minutes", 62167, "Gradminuter", 1, None, None),
    ModbusSensor("immersion_upper_kw", 62168, "Elpatron övre", 0.1, "kW", _P),
    ModbusSensor("immersion_lower_kw", 62169, "Elpatron nedre", 0.1, "kW", _P),
    ModbusSensor("current_l1", 62171, "Ström L1", 0.1, "A", "current"),
    ModbusSensor("current_l2", 62172, "Ström L2", 0.1, "A", "current"),
    ModbusSensor("current_l3", 62173, "Ström L3", 0.1, "A", "current"),
    ModbusSensor("immersion_kwh", 62191, "Elpatron energi", 1, "kWh", _E, "total_increasing"),
    ModbusSensor("hp1_rps", 62193, "Kompressorvarvtal", 0.1, "rps", None),
    ModbusSensor("room_temp_1", 62203, "Rumstemperatur", 0.1, "°C", _T),
    ModbusSensor("room_temp_2", 62204, "Rumstemperatur 2", 0.1, "°C", _T, enabled_default=False),
    ModbusSensor("compressor_hours", 62214, "Kompressordrifttid", 1, "h", None, "total_increasing", count=2),
    ModbusSensor("compressor_hours_24h", 62234, "Kompressordrift senaste dygnet", 1, "min", None, enabled_default=False),
    ModbusSensor("hs1_status", 62246, "Värmesystem status", 1, None, None, None, enum=STATUS_HEATING_SYSTEM),
    ModbusSensor("tank_lower_setpoint", 62274, "Nedre tank börvärde", 0.1, "°C", _T, enabled_default=False),
    ModbusSensor("dhw_lower_temp", 62275, "Varmvatten nedre", 0.1, "°C", _T, enabled_default=False),
    ModbusSensor("dhw_temp", 62276, "Varmvatten", 0.1, "°C", _T),
    ModbusSensor("dhw_capacity", 62279, "Varmvattenkapacitet", 1, "%", None),
    ModbusSensor("sg_mode", 62301, "SmartGrid-läge", 1, None, None, None, enum=SG_MODE),
    # 62331 is documented as supplied power per heat pump. On an i550 Pro it
    # reads 65.5 with the compressor stopped, which cannot be kilowatts, so it
    # is off by default until it can be confirmed on a running unit.
    ModbusSensor("hp1_power", 62331, "Tillförd effekt värmepump (62331)", 0.1, "kW", _P, enabled_default=False),
    ModbusSensor("compressor_kwh", 62341, "Kompressorenergi", 1, "kWh", _E, "total_increasing", count=2),
)


# Stored settings, the 61500 block. Exposed read only. CTC warns explicitly that
# this block lives in EEPROM with a limited number of write cycles, so the
# integration never writes here.
MODBUS_SETTINGS: Final[tuple[ModbusSensor, ...]] = (
    ModbusSensor("set_dhw_mode", 61500, "Inställt varmvattenläge", 1, None, None, None, enum=DHW_MODE),
    ModbusSensor("set_extra_dhw", 61503, "Extra varmvatten kvar", 0.5, "h", None, enabled_default=False),
    ModbusSensor("set_room_1", 61509, "Inställd rumstemperatur", 0.1, "°C", _T),
    ModbusSensor("set_slope_1", 61513, "Kurvlutning", 0.1, None, None, enabled_default=False),
    ModbusSensor("set_adjust_1", 61517, "Kurvjustering", 0.1, None, None, enabled_default=False),
    ModbusSensor("set_hp1_blocked", 61521, "Värmepump tillåten", 1, None, None, None, enabled_default=False),
    ModbusSensor("set_heating_mode_1", 61542, "Inställt värmeläge", 1, None, None, None, enum=HEATING_MODE),
    ModbusSensor("set_max_rps_1", 61572, "Inställt max varvtal", 0.1, "rps", None, enabled_default=False),
    ModbusSensor("set_max_immersion_lower", 61590, "Max elpatron nedre", 0.1, "kW", _P, enabled_default=False),
    ModbusSensor("set_max_immersion_upper", 61591, "Max elpatron övre", 0.1, "kW", _P, enabled_default=False),
)


@dataclass(frozen=True)
class ControlRegister:
    """A volatile control register from CTC's 1000 block.

    These are write only. They are not stored in EEPROM, they can be written as
    often as wanted, and the unit forgets them roughly five minutes after the
    last write. That expiry is the safety net: if Home Assistant stops, the heat
    pump returns to its own settings on its own.
    """

    key: str
    address: int
    name: str
    scale: float = 1.0
    unit: str | None = None
    minimum: float = 0
    maximum: float = 100
    step: float = 1
    enum: dict[int, str] | None = None
    mirror: int | None = None  # register holding the unit's own setting
    icon: str | None = None


CONTROL_NUMBERS: Final[tuple[ControlRegister, ...]] = (
    ControlRegister("ctl_max_rps", 1002, "Styr max varvtal", 0.1, "rps", 0, 120, 1, mirror=61572, icon="mdi:speedometer"),
    ControlRegister("ctl_immersion_lower", 1003, "Styr max elpatron nedre", 0.1, "kW", 0, 9, 0.1, mirror=61590, icon="mdi:heating-coil"),
    ControlRegister("ctl_immersion_upper", 1004, "Styr max elpatron övre", 0.1, "kW", 0, 9, 0.1, mirror=61591, icon="mdi:heating-coil"),
    ControlRegister("ctl_room_setpoint_1", 1010, "Styr rumsbörvärde", 0.1, "°C", 10, 30, 0.1, mirror=61509, icon="mdi:home-thermometer"),
    ControlRegister("ctl_dhw_setpoint", 1033, "Styr varmvattenbörvärde", 0.1, "°C", 30, 65, 1, icon="mdi:water-thermometer"),
    ControlRegister("ctl_extra_dhw", 1006, "Styr extra varmvatten", 0.5, "h", 0, 12, 0.5, mirror=61503, icon="mdi:water-plus"),
)

CONTROL_SELECTS: Final[tuple[ControlRegister, ...]] = (
    ControlRegister("ctl_price_mode", 1005, "Styr elprisläge", enum=PRICE_MODE, icon="mdi:cash-clock"),
    ControlRegister("ctl_dhw_mode", 1007, "Styr varmvattenläge", enum=DHW_MODE, mirror=61500, icon="mdi:water-boiler"),
    ControlRegister("ctl_zone_mode_1", 1015, "Styr driftläge VS1", enum=ZONE_MODE, icon="mdi:radiator"),
)

# Virtual digital inputs. Bit 0 to 7 map to "BMS Di 0" to "BMS Di 7" which are
# assigned to functions such as SmartGrid A and B in the unit's own
# "Definiera / Fjärrstyrning" menu.
CONTROL_VDI_REGISTER: Final = 1100
VDI_COUNT: Final = 8

# How often the volatile control registers are refreshed. CTC requires at least
# every five minutes; a minute leaves a wide margin.
CONTROL_KEEPALIVE_SECONDS: Final = 60


@dataclass
class SlowValue:
    """One value harvested from the display's web interface."""

    key: str
    label: str
    page: int
    screen: int
    fmt: str
    var_indices: list[int] = field(default_factory=list)
    unit: str | None = None
    scale: float = 1.0


@dataclass
class SlowPage:
    """A menu page of the display that can be polled as a unit.

    ``route`` is the sequence of taps that reaches this page from the operation
    data root, recorded while the menu was explored. Replaying a known route is
    far more robust than trying to work the layout out again at poll time, since
    an i255 and an i550 Pro lay their operation data pages out differently.
    """

    page: int
    title: str
    screens: list[int] = field(default_factory=list)
    values: list[SlowValue] = field(default_factory=list)
    route: list[tuple[int, int]] = field(default_factory=list)
