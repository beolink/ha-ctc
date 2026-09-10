"""The CTC EcoZenith integration.

Reads a CTC heat pump locally over Modbus TCP, and optionally harvests the extra
values that only the display knows from its own web interface. Nothing goes near
myUplink or any other cloud.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.loader import async_get_integration

from homeassistant.helpers.storage import Store

from .catalogue import pages_from_storage
from .cop import CopTracker, find_energy_totals, find_operating_hours
from .const import (
    CONF_ENABLE_CONTROL,
    CONF_FAST_INTERVAL,
    CONF_IDENTITY,
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
from .identity import Identity, async_read_identity
from .modbus_api import CtcModbusClient
from .stats import async_setup_stats, async_stop_stats
from .stats_extra import ErrorCounter, build_extra
from .web_api import CtcWebClient

_LOGGER = logging.getLogger(__name__)

#: How often the two lifetime counters are written down. Only one sample a day
#: is kept, so this is about not missing a day rather than about resolution.
COP_SAMPLE_INTERVAL = timedelta(hours=6)


def current_totals(runtime: "CtcRuntime") -> tuple[float | None, float | None]:
    """The two lifetime counters as the display last reported them."""
    if runtime.web is None:
        return None, None
    data = runtime.web.data or {}
    out = data.get(runtime.energy_out.key) if runtime.energy_out else None
    consumed = data.get(runtime.energy_in.key) if runtime.energy_in else None
    return out, consumed


_FAILURES: dict[str, ErrorCounter] = {}


def _stats_extra_for(hass: HomeAssistant, entry: CtcConfigEntry) -> dict[str, Any]:
    """The integration's part of the anonymous daily report.

    Resolved when the report is built, not when it is armed: the controller
    allows a single Modbus client, so a busy or absent controller makes the
    set-up raise and Home Assistant retries it for as long as that lasts.
    The report has to say "installed and unreachable" rather than nothing at
    all. It never opens a connection of its own, it reads what the
    coordinators already have. See stats_extra.py for exactly what is sent.
    """
    runtime = getattr(entry, "runtime_data", None)
    failures = _FAILURES.setdefault(entry.entry_id, ErrorCounter())
    if runtime is None:
        # Set-up has not finished. The model is the one thing the config
        # knows; a read failure is recorded so a controller that never
        # answers is visible rather than silent.
        return build_extra(
            entry.data.get("model"),
            has_display=False,
            control_enabled=False,
            page_count=0,
            read_failures=1,
        )
    cop_day, cop_year, cop_lifetime = cop_for_report(runtime)
    return build_extra(
        entry.data.get("model"),
        has_display=runtime.web is not None,
        control_enabled=runtime.control_enabled,
        page_count=len(runtime.pages),
        read_failures=failures.delta(runtime.modbus.read_failures),
        heatpump_model=runtime.identity.heatpump_model,
        serial=runtime.identity.serial,
        display_firmware=runtime.identity.display_firmware,
        heatpump_firmware=runtime.identity.heatpump_firmware,
        control_firmware=(runtime.modbus.data or {}).get("control_sw"),
        cop_day=cop_day,
        cop_year=cop_year,
        cop_lifetime=cop_lifetime,
    )


async def _async_arm_statistics(hass: HomeAssistant, entry: CtcConfigEntry) -> None:
    """Arm the daily report before the first Modbus call.

    Home Assistant runs an entry's on-unload callbacks after every failed
    set-up attempt and retries for as long as the controller stays away, so a
    reporter armed at the end of a successful set-up goes quiet exactly then.
    Stopped only from async_unload_entry, which a failed attempt never
    reaches. On unless the user switches it off in the options.
    """
    try:
        integration = await async_get_integration(hass, DOMAIN)
        await async_setup_stats(
            hass, entry, DOMAIN, str(integration.version),
            extra=lambda: _stats_extra_for(hass, entry),
        )
    except Exception:  # noqa: BLE001 - statistics must never break a set-up
        _LOGGER.debug("Could not arm the statistics reporter", exc_info=True)


def commissioning_date(runtime: "CtcRuntime"):
    """Work out when the lifetime counters started, from the powered-on hours.

    CTC counts the hours the unit has been switched on. Taken back from today
    they land on the day it was commissioned, which is also the day both energy
    counters stood at zero. The hours stop while the unit is off, so the answer
    can only come out late, never early, and the tracker keeps the earliest one.
    """
    from datetime import date, timedelta

    if runtime.web is None or not runtime.operating_hours:
        return None
    data = runtime.web.data or {}
    readings = [data.get(v.key) for v in runtime.operating_hours]
    hours = max((h for h in readings if isinstance(h, (int, float)) and h > 0), default=None)
    if hours is None:
        return None
    return date.today() - timedelta(hours=hours)


def cop_for_report(
    runtime: "CtcRuntime",
) -> tuple[float | None, float | None, float | None, float | None]:
    """Return the figures over a day, a rolling year and the whole lifetime.

    Each is only returned when it actually stands on its own span. Sending the
    lifetime figure under a yearly name would be a different number wearing the
    wrong label.
    """
    if runtime.cop is None:
        return None, None, None, None
    out, consumed = current_totals(runtime)
    result = runtime.cop.result(out, consumed)
    yearly = result.value if result.basis == "year" else None
    daily = runtime.cop.result_day(out, consumed).value
    first_year = runtime.cop.result_first_year().value
    lifetime = None
    if out is not None and consumed is not None and consumed >= 50:
        lifetime = round(out / consumed, 2)
    return daily, yearly, first_year, lifetime


@dataclass
class CtcRuntime:
    """Everything one config entry needs at runtime."""

    modbus: CtcModbusCoordinator
    control: CtcControlManager
    device: DeviceInfo
    web: CtcWebCoordinator | None = None
    pages: list[SlowPage] = field(default_factory=list)
    control_enabled: bool = False
    identity: Identity = field(default_factory=Identity)
    cop: CopTracker | None = None
    #: The two lifetime counters, once they turn up among the harvested pages.
    energy_out: Any | None = None
    energy_in: Any | None = None
    #: Candidate rows for the unit's powered-on hours; the largest is used.
    operating_hours: Any | None = None


type CtcConfigEntry = ConfigEntry[CtcRuntime]


async def async_setup_entry(hass: HomeAssistant, entry: CtcConfigEntry) -> bool:
    """Set up one heat pump."""
    host = entry.data[CONF_HOST]
    modbus_port = entry.data.get(CONF_MODBUS_PORT, DEFAULT_MODBUS_PORT)
    web_port = entry.data.get(CONF_WEB_PORT, DEFAULT_WEB_PORT)
    slave = entry.data.get(CONF_SLAVE, DEFAULT_SLAVE)
    options = entry.options

    await _async_arm_statistics(hass, entry)

    modbus_client = CtcModbusClient(host, modbus_port, slave)
    modbus = CtcModbusCoordinator(
        hass,
        modbus_client,
        int(options.get(CONF_FAST_INTERVAL, DEFAULT_FAST_INTERVAL)),
    )
    try:
        await modbus.async_config_entry_first_refresh()
    except Exception:
        # The controller allows a single Modbus client. A failed attempt that
        # leaves its socket open holds that slot, so every retry then fails as
        # well and the entry can never recover on its own.
        await modbus_client.async_close()
        raise

    web_client = CtcWebClient(
        async_get_clientsession(hass),
        host,
        web_port,
        int(options.get(CONF_LANGUAGE, LANG_SWEDISH)),
    )

    # What the unit is, rather than what it is doing. Static, so it is read once
    # and kept: the panel writes it into its own screens and never changes it.
    identity = Identity.from_dict(options.get(CONF_IDENTITY))
    if identity.is_empty:
        try:
            identity = await async_read_identity(web_client)
        except Exception as err:  # noqa: BLE001 - identity is nice to have
            _LOGGER.debug("Could not read the unit's identity: %s", err)
        if not identity.is_empty:
            hass.config_entries.async_update_entry(
                entry, options={**options, CONF_IDENTITY: identity.as_dict()}
            )

    model = entry.data.get("model", "CTC")
    device = DeviceInfo(
        identifiers={(DOMAIN, host)},
        manufacturer="CTC / Enertech",
        model=f"{model} + {identity.heatpump_model}" if identity.heatpump_model else model,
        # The device name becomes the prefix of every entity id, so it stays
        # short. The entry title keeps the address for telling two units apart.
        name=f"CTC {model}",
        serial_number=identity.serial,
        sw_version=identity.display_firmware,
        hw_version=identity.bootloader,
        configuration_url=f"http://{host}:{web_port}/main.html",
    )

    runtime = CtcRuntime(
        modbus=modbus,
        control=CtcControlManager(hass, modbus_client),
        device=device,
        control_enabled=bool(options.get(CONF_ENABLE_CONTROL, False)),
        identity=identity,
    )

    pages = pages_from_storage(options.get(CONF_SLOW_PAGES, []))
    if pages:
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
        runtime.energy_out, runtime.energy_in = find_energy_totals(pages)
        runtime.operating_hours = find_operating_hours(pages)
        if runtime.energy_out is not None and runtime.energy_in is not None:
            runtime.cop = CopTracker(
                Store(hass, 1, f"{DOMAIN}_{entry.entry_id}_cop")
            )
            await runtime.cop.async_load()

    entry.runtime_data = runtime
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await modbus_client.async_close()
        raise
    entry.async_on_unload(entry.add_update_listener(_async_reload))

    if runtime.cop is not None:
        from homeassistant.helpers.event import async_track_time_interval

        async def _record_cop(_now=None) -> None:
            out, consumed = current_totals(runtime)
            try:
                commissioned = commissioning_date(runtime)
                if commissioned is not None:
                    await runtime.cop.async_set_anchor(commissioned)  # type: ignore[union-attr]
                await runtime.cop.async_record(out, consumed)  # type: ignore[union-attr]
            except Exception as err:  # noqa: BLE001 - a missed sample is not fatal
                _LOGGER.debug("Could not write down the energy counters: %s", err)

        await _record_cop()
        entry.async_on_unload(
            async_track_time_interval(hass, _record_cop, COP_SAMPLE_INTERVAL)
        )

    return True


async def _async_reload(hass: HomeAssistant, entry: CtcConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: CtcConfigEntry) -> bool:
    """Tear one heat pump down, releasing the single Modbus slot."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        runtime = entry.runtime_data
        # Only here, never from an on-unload callback: those also run when a
        # set-up attempt fails, and the report has to survive that.
        await async_stop_stats(hass, entry, DOMAIN)
        await runtime.control.async_stop()
        await runtime.modbus.client.async_close()
    return unloaded
