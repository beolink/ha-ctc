"""Anonymous usage statistics for this integration.

Copy the file to custom_components/<domain>/stats.py and wire it in as
described in the backend repo. It sends one report per day to stats.rnet.se:
version, Home Assistant version, country as configured in Home Assistant
itself, entity counts and a few on/off flags. Never a name, an address, a
position, a serial number, an entity id or a measurement from the house.

Reporting is on by default and is switched off in the integration's options.
Switching it off sends one last call that erases everything stored about this
particular installation.

What is collected, and why: https://stats.rnet.se/integritet
"""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from datetime import timedelta
from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.system_info import async_get_system_info

_LOGGER = logging.getLogger(__name__)

ENDPOINT = "https://stats.rnet.se/api/v1/report"
SCHEMA_VERSION = 1

#: Option key that controls reporting. On unless the user turns it off.
OPTION_KEY = "send_statistics"

INTERVAL = timedelta(hours=24)
FIRST_DELAY = timedelta(minutes=10)
TIMEOUT = 10

#: Only these keys may be added by the integration's own extra callback. The
#: backend rejects anything else, but stopping it here keeps a careless caller
#: from ever putting house data on the wire in the first place.
EXTRA_KEYS = ("models", "features", "errors")

_INSTALL_TYPES = {
    "Home Assistant OS": "os",
    "Home Assistant Supervised": "supervised",
    "Home Assistant Container": "container",
    "Home Assistant Core": "core",
}


def stats_enabled(entry: ConfigEntry) -> bool:
    """True unless the user has switched reporting off."""
    return bool(entry.options.get(OPTION_KEY, True))


class StatsReporter:
    """Sends one anonymous report per day.

    It must never disturb the integration it lives in: every failure is
    swallowed and logged at debug level.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        domain: str,
        version: str,
        extra: Callable[[], dict[str, Any]] | None = None,
        endpoint: str = ENDPOINT,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.domain = domain
        self.version = version
        self.extra = extra
        self.endpoint = endpoint
        self._store: Store = Store(hass, 1, f"{domain}.stats")
        self._install_id: str | None = None
        self._cancel: list[Callable[[], None]] = []
        self._logged_once = False

    async def _async_install_id(self) -> str:
        """A random id that exists only in this installation. The server never
        stores it in the clear, only as an HMAC."""
        if self._install_id:
            return self._install_id
        data = await self._store.async_load() or {}
        install_id = data.get("install_id")
        if not install_id:
            install_id = str(uuid.uuid4())
            await self._store.async_save({"install_id": install_id})
        self._install_id = install_id
        return install_id

    async def async_start(self) -> None:
        """Start reporting unless it is switched off."""
        if not stats_enabled(self.entry):
            _LOGGER.debug("Statistics disabled for %s", self.domain)
            return

        async def _first(_now) -> None:
            await self.async_report()
            self._cancel.append(
                async_track_time_interval(self.hass, self.async_report, INTERVAL)
            )

        # Spread out, so every installation does not call home at once.
        delay = FIRST_DELAY.total_seconds() + random.uniform(0, 900)
        self._cancel.append(async_call_later(self.hass, delay, _first))

    async def async_stop(self) -> None:
        """Cancel the timers. Called from async_unload_entry."""
        for cancel in self._cancel:
            cancel()
        self._cancel.clear()

    async def async_payload(self) -> dict[str, Any]:
        """Build the payload. Everything in it is deliberately coarse."""
        info: dict[str, Any] = {}
        try:
            info = await async_get_system_info(self.hass)
        except Exception:  # pragma: no cover - depends on the HA version
            pass

        entities = 0
        devices = 0
        try:
            registry = er.async_get(self.hass)
            entities = len(er.async_entries_for_config_entry(registry, self.entry.entry_id))
            device_reg = dr.async_get(self.hass)
            devices = len(dr.async_entries_for_config_entry(device_reg, self.entry.entry_id))
        except Exception:  # pragma: no cover
            pass

        language = (self.hass.config.language or "")[:2].lower() or None
        country = self.hass.config.country or None

        payload: dict[str, Any] = {
            "schema": SCHEMA_VERSION,
            "install_id": await self._async_install_id(),
            "integration": self.domain,
            "version": self.version,
            "ha_version": HA_VERSION,
            "ha_type": _INSTALL_TYPES.get(info.get("installation_type", ""), "unknown"),
            "python_version": info.get("python_version"),
            "country": country,
            "language": language,
            "entities": entities,
            "devices": devices,
        }
        if self.extra:
            try:
                for key, value in (self.extra() or {}).items():
                    if key in EXTRA_KEYS:
                        payload[key] = value
            except Exception:  # pragma: no cover
                _LOGGER.debug("Could not collect extra statistics", exc_info=True)

        return {k: v for k, v in payload.items() if v is not None}

    async def async_report(self, _now=None) -> None:
        """Send one report. Goes quiet at the slightest problem."""
        if not stats_enabled(self.entry):
            return
        try:
            payload = await self.async_payload()
            if not self._logged_once:
                self._logged_once = True
                _LOGGER.info(
                    "Sending anonymous statistics to %s: %s. Switch it off under "
                    "Settings, Devices and services, %s, Configure.",
                    self.endpoint,
                    payload,
                    self.domain,
                )
            session = async_get_clientsession(self.hass)
            async with asyncio.timeout(TIMEOUT):
                async with session.post(self.endpoint, json=payload) as resp:
                    if resp.status >= 400:
                        _LOGGER.debug("Statistics rejected: %s", resp.status)
        except Exception:  # noqa: BLE001 - statistics must never break anything
            _LOGGER.debug("Could not send statistics", exc_info=True)

    async def async_forget(self) -> None:
        """Ask the server to erase everything about this installation. Called
        when the user switches reporting off."""
        try:
            install_id = await self._async_install_id()
            session = async_get_clientsession(self.hass)
            body = {
                "schema": SCHEMA_VERSION,
                "integration": self.domain,
                "install_id": install_id,
            }
            async with asyncio.timeout(TIMEOUT):
                async with session.delete(self.endpoint, json=body) as resp:
                    _LOGGER.debug("Forget me answered %s", resp.status)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Could not send forget me", exc_info=True)


async def async_setup_stats(
    hass: HomeAssistant,
    entry: ConfigEntry,
    domain: str,
    version: str,
    extra: Callable[[], dict[str, Any]] | None = None,
) -> StatsReporter:
    """Create, start and return the reporter."""
    reporter = StatsReporter(hass, entry, domain, version, extra)
    await reporter.async_start()
    return reporter


async def async_forget_install(hass: HomeAssistant, entry: ConfigEntry, domain: str) -> None:
    """Ask the server to forget this installation. Called when the user
    switches reporting off in the options."""
    await StatsReporter(hass, entry, domain, "0").async_forget()
