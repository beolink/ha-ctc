"""Anonymous usage statistics for this integration.

Copy the file to custom_components/<domain>/stats.py and wire it in as
described in the backend repo. It sends one report per day to stats.rnet.se:
version, Home Assistant version, country as configured in Home Assistant
itself, entity counts, a few on/off flags and an approximate position rounded
to about 11 km. Never a name, an address, an exact position, a serial number,
an entity id or anything at a finer resolution than one day.

Reporting is on by default and is switched off in the integration's options.
Switching it off sends one last call that erases everything stored about this
particular installation.

The reporter is armed once per config entry and kept in ``hass.data``, so it
outlives a set-up that does not finish. An integration whose device is
unreachable at start-up raises ConfigEntryNotReady and is retried by Home
Assistant for as long as the device stays away; if the reporter were tied to
that attempt, the installation would fall silent exactly while something is
wrong with it. Arm it before the first call that can raise, and stop it from
``async_unload_entry`` (which a failed attempt never reaches).

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

#: Where the armed reporters live, keyed by (domain, entry_id).
DATA_KEY = "rnet_stats_reporters"

INTERVAL = timedelta(hours=24)
FIRST_DELAY = timedelta(minutes=10)
TIMEOUT = 10

#: Only these keys may be added by the integration's own extra callback. The
#: backend rejects anything else, but stopping it here keeps a careless caller
#: from ever putting house data on the wire in the first place.
#: "firmwares" is the per-board object ({"display": ..., "heatpump": ...,
#: "control": ...}) that schema 2 added and the backend has accepted since; it
#: was missing here, so every integration sending it had its firmware dropped
#: before the wire.
EXTRA_KEYS = ("models", "features", "errors", "metrics", "firmware", "firmwares")

#: Decimals kept of the position. One decimal is roughly 11 km, which is enough
#: for climate, price area and a readable map, and far too coarse to point at a
#: house. The rounding happens here, so a finer value never leaves the machine.
POSITION_DECIMALS = 1

_INSTALL_TYPES = {
    "Home Assistant OS": "os",
    "Home Assistant Supervised": "supervised",
    "Home Assistant Container": "container",
    "Home Assistant Core": "core",
}


def _coarse(value: float | None) -> float | None:
    """Round a coordinate to the reporting grid, or drop it if it is missing."""
    if value is None:
        return None
    try:
        return round(float(value), POSITION_DECIMALS)
    except (TypeError, ValueError):
        return None


def stats_enabled(entry: ConfigEntry) -> bool:
    """True unless the user has switched reporting off.

    Options win, then data: some integrations keep their settings in the entry
    data and only ever write an empty options dict, and the switch must work
    the same way in all of them.
    """
    options = getattr(entry, "options", None) or {}
    if OPTION_KEY in options:
        return bool(options[OPTION_KEY])
    data = getattr(entry, "data", None) or {}
    return bool(data.get(OPTION_KEY, True))


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
        name: str | None = None,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.domain = domain
        self.version = version
        # What a person sees in the log line. The wire payload is keyed on the
        # domain, which never changes; the display name may.
        self.name = name or domain
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
        """Cancel the timers. Safe to call more than once."""
        for cancel in self._cancel:
            cancel()
        self._cancel.clear()

    @property
    def armed(self) -> bool:
        """Whether the timers are running."""
        return bool(self._cancel)

    def rearm_source(self, version: str, extra=None, name: str | None = None) -> None:
        """Point an already armed reporter at the current set-up attempt: the
        callback and the version may have changed since it was armed."""
        self.version = version
        if extra is not None:
            self.extra = extra
        if name:
            self.name = name

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
        lat = _coarse(getattr(self.hass.config, "latitude", None))
        lon = _coarse(getattr(self.hass.config, "longitude", None))

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
            "lat": lat,
            "lon": lon,
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
                    self.name,
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
    name: str | None = None,
) -> StatsReporter:
    """Arm the reporter for this entry, or return the one already armed.

    Call it early in ``async_setup_entry``, before the first call that can
    raise ConfigEntryNotReady. A second call for the same entry (Home
    Assistant retrying a set-up whose device is unreachable) updates the
    callback and returns the running reporter instead of arming a second one,
    so the daily report keeps going out while the device is away.

    The caller must NOT register ``entry.async_on_unload(reporter.async_stop)``:
    Home Assistant runs those callbacks when a set-up attempt fails, which is
    the case this is here to survive. Stop it from ``async_unload_entry``
    with :func:`async_stop_stats` instead.
    """
    reporters: dict = hass.data.setdefault(DATA_KEY, {})
    key = (domain, entry.entry_id)
    existing: StatsReporter | None = reporters.get(key)
    if existing is not None and existing.armed:
        existing.rearm_source(version, extra, name)
        return existing
    reporter = StatsReporter(hass, entry, domain, version, extra, name=name)
    reporters[key] = reporter
    await reporter.async_start()
    return reporter


async def async_stop_stats(hass: HomeAssistant, entry: ConfigEntry, domain: str) -> None:
    """Stop and forget the entry's reporter. Call it from
    ``async_unload_entry``, never from an ``entry.async_on_unload`` callback."""
    reporter = (hass.data.get(DATA_KEY) or {}).pop((domain, entry.entry_id), None)
    if reporter is not None:
        await reporter.async_stop()


async def async_forget_install(hass: HomeAssistant, entry: ConfigEntry, domain: str) -> None:
    """Ask the server to forget this installation. Called when the user
    switches reporting off in the options."""
    await StatsReporter(hass, entry, domain, "0").async_forget()
