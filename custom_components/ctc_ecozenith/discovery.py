"""Find CTC displays on the local network.

The display has no mDNS, no reverse DNS and a locally administered MAC in the
02:00:00:00 range, so there is nothing to look up. What it does have is a
distinctive fingerprint: port 80 answers 400 to almost everything, but
``/settings/name`` returns the settings file name, which is ``settings_ezi2xx.bin``
on an EcoZenith i255 and ``settings_ezi5xx.bin`` on an i550 Pro.

Scanning is therefore a two stage sweep: open the TCP port on every address in
the candidate networks, then ask the ones that answer for that file name.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from dataclasses import dataclass

import aiohttp

_LOGGER = logging.getLogger(__name__)

PORT_CONCURRENCY = 60
PROBE_CONCURRENCY = 8
PORT_TIMEOUT = 0.4
PROBE_TIMEOUT = 4.0

MODEL_NAMES = {
    "ezi2xx": "EcoZenith i250 / i255",
    "ezi3xx": "EcoZenith i350 / i360",
    "ezi5xx": "EcoZenith i550 Pro / i555 Pro",
    "ecologic": "EcoLogic",
}


@dataclass
class DiscoveredDisplay:
    """A CTC display found on the network."""

    host: str
    settings_name: str

    @property
    def model(self) -> str:
        stem = self.settings_name.removeprefix("settings_").removesuffix(".bin")
        return MODEL_NAMES.get(stem, f"CTC ({stem})")

    @property
    def label(self) -> str:
        return f"{self.host} — {self.model}"


def local_networks(max_hosts: int = 512) -> list[ipaddress.IPv4Network]:
    """Return the /24 networks around this machine's own addresses."""
    networks: list[ipaddress.IPv4Network] = []
    seen: set[str] = set()
    for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
        address = info[4][0]
        if address.startswith("127."):
            continue
        try:
            candidate = ipaddress.ip_network(f"{address}/24", strict=False)
        except ValueError:
            continue
        if str(candidate) in seen or candidate.num_addresses > max_hosts:
            continue
        seen.add(str(candidate))
        networks.append(candidate)  # type: ignore[arg-type]
    if not networks:
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect(("10.255.255.255", 1))
            address = probe.getsockname()[0]
            probe.close()
            networks.append(ipaddress.ip_network(f"{address}/24", strict=False))  # type: ignore[arg-type]
        except OSError:
            _LOGGER.debug("Could not determine a local network to scan")
    return networks


async def _port_open(host: str, port: int, timeout: float = PORT_TIMEOUT) -> bool:
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
    except (asyncio.TimeoutError, OSError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except (OSError, asyncio.TimeoutError):  # pragma: no cover
        pass
    return True


async def async_probe_host(
    session: aiohttp.ClientSession, host: str, port: int = 80
) -> DiscoveredDisplay | None:
    """Ask one host whether it is a CTC display."""
    url = f"http://{host}:{port}/settings/name"
    try:
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=PROBE_TIMEOUT)
        ) as response:
            if response.status != 200:
                return None
            body = (await response.text()).strip()
    except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeDecodeError):
        return None
    if body.startswith("settings_") and body.endswith(".bin"):
        return DiscoveredDisplay(host=host, settings_name=body)
    return None


async def async_discover(
    session: aiohttp.ClientSession,
    networks: list[ipaddress.IPv4Network] | None = None,
    port: int = 80,
) -> list[DiscoveredDisplay]:
    """Sweep the local networks and return every CTC display found."""
    if networks is None:
        networks = local_networks()
    if not networks:
        return []

    hosts = [str(ip) for network in networks for ip in network.hosts()]
    open_hosts: list[str] = []
    port_gate = asyncio.Semaphore(PORT_CONCURRENCY)

    async def check(host: str) -> None:
        async with port_gate:
            if await _port_open(host, port):
                open_hosts.append(host)

    await asyncio.gather(*(check(host) for host in hosts))
    _LOGGER.debug("%s hosts answer on port %s", len(open_hosts), port)

    found: list[DiscoveredDisplay] = []
    probe_gate = asyncio.Semaphore(PROBE_CONCURRENCY)

    async def probe(host: str) -> None:
        async with probe_gate:
            display = await async_probe_host(session, host, port)
            if display is not None:
                found.append(display)

    await asyncio.gather(*(probe(host) for host in open_hosts))
    found.sort(key=lambda d: tuple(int(part) for part in d.host.split(".")))
    return found
