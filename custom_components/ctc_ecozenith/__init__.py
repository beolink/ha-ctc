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
from homeassistant.helpers import issue_registry as ir
from homeassistant.loader import async_get_integration

from homeassistant.helpers.storage import Store

from . import dashboard
from .catalogue import async_discover_pages, merge_menu, pages_from_storage, pages_to_storage
from .cop import (
    ConsumptionSnapshot,
    CopTracker,
    cop_for_report,
    current_totals,
    find_energy_totals,
    find_operating_hours,
    modbus_consumption,
)
from .const import (
    CONF_ENABLE_CONTROL,
    CONF_FAST_INTERVAL,
    CONF_IDENTITY,
    CONF_MENU,
    CONF_MENU_VERSION,
    CONF_LANGUAGE,
    CONF_MODBUS_PORT,
    CONF_RESTORE_PAGE,
    CONF_SLAVE,
    CONF_SLOW_INTERVAL,
    CONF_SLOW_PAGES,
    CONF_VISIT_SYSTEM_INFO,
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
from .identity import Identity, async_read_identity, async_read_identity_via_panel
from .modbus_api import CtcModbusClient
from .seen import SeenValues
from .seen_history import async_seed_from_statistics
from .stats import async_setup_stats, async_stop_stats
from .stats_extra import ErrorCounter, build_extra
from .web_api import CtcWebClient

_LOGGER = logging.getLogger(__name__)

#: How often the two lifetime counters are written down. Only one sample a day
#: is kept, so this is about not missing a day rather than about resolution.
COP_SAMPLE_INTERVAL = timedelta(hours=6)




_FAILURES: dict[str, ErrorCounter] = {}

#: Entries whose panel has been walked to the system information page in this
#: run. The walk moves the display, so it is attempted once, not at every
#: reload, and only while the identity is still missing.
_WALKED: set[str] = set()

#: Entries whose menu has been read again in this run. A reading that failed
#: because the display was busy is worth another try after a restart, but not
#: at every reload: walking the menu moves the panel.
_MENU_READ: set[str] = set()

ISSUE_HISTORY_PAGE = "history_page_missing"
ISSUE_IDENTITY = "identity_incomplete"


def _async_review_issues(
    hass: HomeAssistant, entry: "CtcConfigEntry", runtime: "CtcRuntime"
) -> None:
    """Say in the repairs view what only the owner can settle.

    Two things the integration cannot do for itself: which pages the panel may
    be walked to, and showing the system information page once so the display
    writes its serial number into it.
    """
    def review(key: str, needed: bool) -> None:
        issue_id = f"{entry.entry_id}_{key}"
        if needed:
            ir.async_create_issue(
                hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=key,
            )
        else:
            ir.async_delete_issue(hass, DOMAIN, issue_id)

    review(ISSUE_HISTORY_PAGE, runtime.web is not None and runtime.energy_out is None)
    review(ISSUE_IDENTITY, not runtime.identity.serial)


async def _async_catch_up(
    hass: HomeAssistant,
    entry: "CtcConfigEntry",
    runtime: "CtcRuntime",
    client: CtcWebClient,
    version: str,
) -> None:
    """Read the menu again after an update, and fill in a missing identity.

    Both move the physical panel, so neither runs during set-up and neither runs
    while the harvester is walking: the panel lock keeps them apart. A new
    version reads the whole menu again, because a newer parser can make sense of
    rows and pages the old one passed over, and pages nobody has switched off
    are harvested.
    """
    changed: dict[str, Any] = {}
    options = entry.options
    try:
        if options.get(CONF_MENU_VERSION) != version and entry.entry_id not in _MENU_READ:
            _MENU_READ.add(entry.entry_id)
            async with client.panel:
                discovered = await async_discover_pages(client)
            if discovered:
                menu, selected = merge_menu(
                    pages_from_storage(options.get(CONF_MENU)),
                    [page.page for page in pages_from_storage(options.get(CONF_SLOW_PAGES))],
                    discovered,
                )
                chosen = set(selected)
                changed[CONF_MENU] = pages_to_storage(menu)
                changed[CONF_SLOW_PAGES] = pages_to_storage(
                    [page for page in menu if page.page in chosen]
                )
                # Only a reading that worked counts as done. A display that was
                # busy is tried again after a restart, not at every reload.
                changed[CONF_MENU_VERSION] = version
            else:
                _LOGGER.debug("The display's menu could not be read; keeping the stored one")

        if (
            not runtime.identity.serial
            and options.get(CONF_VISIT_SYSTEM_INFO, True)
            and entry.entry_id not in _WALKED
        ):
            _WALKED.add(entry.entry_id)
            async with client.panel:
                found = await async_read_identity_via_panel(
                    client,
                    restore=runtime.web.async_restore_page if runtime.web else None,
                )
            merged = runtime.identity.merged_with(found)
            if merged.as_dict() != runtime.identity.as_dict():
                changed[CONF_IDENTITY] = merged.as_dict()
    except Exception as err:  # noqa: BLE001 - catching up must never break the entry
        _LOGGER.debug("Could not catch up with the display: %s", err)

    if changed:
        # Writing the options reloads the entry, which is where the new pages
        # and the new identity are picked up.
        hass.config_entries.async_update_entry(entry, options={**entry.options, **changed})


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
        history_page=bool(runtime.operating_hours),
        heat_counter=runtime.energy_out is not None,
        consumption_counter=runtime.energy_in is not None,
        consumption_modbus=modbus_consumption(runtime.modbus.data) is not None,
        **cop_for_report(runtime),
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
    #: Consumed energy from Modbus, where the display has no counter for it.
    consumption_snapshot: ConsumptionSnapshot | None = None
    #: Candidate rows for the unit's powered-on hours; the largest is used.
    operating_hours: Any | None = None
    #: What this installation has ever given a value other than zero.
    seen: SeenValues | None = None


type CtcConfigEntry = ConfigEntry[CtcRuntime]


async def async_setup_entry(hass: HomeAssistant, entry: CtcConfigEntry) -> bool:
    """Set up one heat pump."""
    host = entry.data[CONF_HOST]
    modbus_port = entry.data.get(CONF_MODBUS_PORT, DEFAULT_MODBUS_PORT)
    web_port = entry.data.get(CONF_WEB_PORT, DEFAULT_WEB_PORT)
    slave = entry.data.get(CONF_SLAVE, DEFAULT_SLAVE)
    options = entry.options

    await _async_arm_statistics(hass, entry)
    # The sidebar page, before the first Modbus call for the same reason as the
    # report: a controller that is away keeps the entry retrying, and the page
    # should say so rather than vanish from the sidebar.
    try:
        integration = await async_get_integration(hass, DOMAIN)
        await dashboard.async_register(hass, str(integration.version))
    except Exception:  # noqa: BLE001 - the page must never break a set-up
        _LOGGER.warning("Could not add the CTC EcoZenith page", exc_info=True)

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
    # The display only writes these values into a screen once that screen has
    # been shown on the panel. Until then they read as empty, so a unit whose
    # system information page nobody has opened reports no firmware at all.
    # Reading again at every start, and only ever filling gaps, means the
    # values turn up by themselves the first time someone opens the page.
    if not identity.is_complete:
        try:
            found = await async_read_identity(web_client)
        except Exception as err:  # noqa: BLE001 - identity is nice to have
            _LOGGER.debug("Could not read the unit's identity: %s", err)
            found = Identity()
        merged = identity.merged_with(found)
        if merged.as_dict() != identity.as_dict():
            identity = merged
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
        if (
            runtime.energy_out is not None
            and runtime.energy_in is None
            and modbus_consumption(modbus.data) is not None
        ):
            # The older display software, as on an i360, counts delivered heat
            # but not consumed energy. Modbus 62341 holds that number, and is
            # taken at the moment the display is read so the two stay a pair.
            # Registered before the platforms, so the sensors that listen to
            # the same coordinator see the new pairing when they update.
            snapshot = ConsumptionSnapshot()
            heat_key = runtime.energy_out.key

            def _take_consumption() -> None:
                snapshot.update(web.read_at.get(heat_key), modbus.data)

            _take_consumption()
            entry.async_on_unload(web.async_add_listener(_take_consumption))
            runtime.consumption_snapshot = snapshot
        if runtime.energy_out is not None and (
            runtime.energy_in is not None or runtime.consumption_snapshot is not None
        ):
            runtime.cop = CopTracker(
                Store(hass, 1, f"{DOMAIN}_{entry.entry_id}_cop")
            )
            await runtime.cop.async_load()

    # What this installation actually has, learnt from what it reports: CTC
    # answers with a clean zero for hardware and registers it does not use.
    seen = SeenValues(
        Store(hass, 1, f"{DOMAIN}_{entry.entry_id}_seen"),
        on_new=lambda: dashboard.async_announce_change(hass),
    )
    await seen.async_load()
    for coordinator in (modbus, runtime.web):
        if coordinator is None:
            continue
        seen.note(coordinator.data)
        entry.async_on_unload(
            coordinator.async_add_listener(
                lambda coordinator=coordinator: seen.note(coordinator.data)
            )
        )
    runtime.seen = seen
    if seen.fresh:
        # Once, with the recorder surely up: what the statistics already show.
        from homeassistant.helpers.start import async_at_started

        async def _seed(_hass: HomeAssistant) -> None:
            await async_seed_from_statistics(hass, entry.entry_id, f"{DOMAIN}_{host}_", seen)

        entry.async_on_unload(async_at_started(hass, _seed))

    entry.runtime_data = runtime
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await modbus_client.async_close()
        raise
    entry.async_on_unload(entry.add_update_listener(_async_reload))

    _async_review_issues(hass, entry, runtime)

    integration = await async_get_integration(hass, DOMAIN)
    entry.async_create_background_task(
        hass,
        _async_catch_up(hass, entry, runtime, web_client, str(integration.version)),
        f"{DOMAIN} catch up",
    )

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

    dashboard.async_announce_change(hass)
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
        dashboard.async_announce_change(hass)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: CtcConfigEntry) -> None:
    """Take the sidebar page away with the last heat pump.

    Not on unload: a reload unloads the entry too, and removing the panel then
    would throw anyone looking at the page back to the start page.
    """
    others = [
        other
        for other in hass.config_entries.async_entries(DOMAIN)
        if other.entry_id != entry.entry_id
    ]
    if not others:
        dashboard.async_unregister(hass)
    # What the unit was seen to have belongs to this entry alone.
    await Store(hass, 1, f"{DOMAIN}_{entry.entry_id}_seen").async_remove()
