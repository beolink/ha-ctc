"""Is there a newer release of this integration to fetch?

Home Assistant tells you about updates for integrations HACS installed, but a
copy put into ``custom_components`` by hand is invisible to it: nothing knows
that a new version exists. The check here asks GitHub for the newest published
release and the answer is shown in the repairs view.

Kept free of Home Assistant imports, so the comparison can be tested without it.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

_LOGGER = logging.getLogger(__name__)

#: A version is a handful of numbers. Anything else is not answered on.
_NUMBERS = re.compile(r"\d+")
_VERSION = re.compile(r"^v?\d+(\.\d+){0,3}$")


def _parts(version: str | None) -> tuple[int, ...]:
    return tuple(int(number) for number in _NUMBERS.findall(version or "")[:4])


def newer(installed: str | None, latest: str | None) -> bool:
    """True when ``latest`` is ahead of ``installed``.

    Both are padded to the same length, so 0.9 and 0.9.0 are the same version
    rather than one being older than the other. An unreadable version is never
    called out of date: saying nothing is better than crying wolf.
    """
    here, there = _parts(installed), _parts(latest)
    if not here or not there:
        return False
    size = max(len(here), len(there))
    here += (0,) * (size - len(here))
    there += (0,) * (size - len(there))
    return there > here


async def async_latest_release(session: Any, url: str, timeout: int = 10) -> str | None:
    """The newest published release, or None when the question cannot be asked.

    GitHub's own endpoint never answers with a draft or a pre-release, so what
    comes back is what a user would be offered. Every failure is swallowed: an
    update check must never disturb the integration.
    """
    try:
        async with asyncio.timeout(timeout):
            async with session.get(
                url, headers={"accept": "application/vnd.github+json"}
            ) as response:
                if response.status != 200:
                    _LOGGER.debug("Release check answered HTTP %s", response.status)
                    return None
                data = await response.json()
    except Exception as err:  # noqa: BLE001 - never break anything over this
        _LOGGER.debug("Could not ask for the newest release: %s", err)
        return None
    tag = data.get("tag_name") if isinstance(data, dict) else None
    if not isinstance(tag, str) or not _VERSION.match(tag.strip()):
        return None
    return tag.strip().lstrip("v")
