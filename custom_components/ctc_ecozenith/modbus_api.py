"""Modbus TCP client for CTC's BMS interface.

CTC documents this in the BMS manual (162 600 16). Everything is a holding
register: function code 3 to read, 16 to write, offset 0, and at most 100
registers per transaction.

The controller accepts exactly one master at a time. It answers the TCP
handshake for a second client and then resets the connection as soon as that
client sends a PDU, which looks like the unit being offline. One connection is
therefore held for the lifetime of the entry and every request is serialised
behind a lock.

CTC also sets a pace. The BMS documentation gives an update rate of 1000 ms and
the controller cannot pipeline, so exactly one request may be outstanding and
requests are spaced out rather than sent back to back. It also needs a moment
after the socket opens before it will answer. Both are enforced here rather than
left to the caller, because getting them wrong looks like an unreliable network
instead of a client that is talking too fast.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .const import SENTINELS, ModbusSensor

_LOGGER = logging.getLogger(__name__)

MAX_BLOCK = 100
CONNECT_TIMEOUT = 10
REQUEST_TIMEOUT = 10

# Shortest gap between two transactions. CTC documents an update rate of one
# second for the BMS interface; the community Modbus configurations that work
# use tens of milliseconds between messages. Sixty is a compromise that keeps a
# full poll brisk without crowding the controller.
MESSAGE_WAIT = 0.06

# The controller needs a moment after the socket opens before it answers. The
# widely used YAML packages wait five seconds; three is enough in practice and
# only costs anything on the first poll after a reconnect.
CONNECT_DELAY = 3.0


class CtcModbusError(Exception):
    """Raised when the Modbus side cannot be used."""


def decode_signed(value: int) -> int:
    """Interpret a 16 bit register as two's complement."""
    return value - 65536 if value > 32767 else value


def decode_pair(low: int, high: int) -> int:
    """Combine a 32 bit value. CTC sends the least significant word first."""
    return (high << 16) | low


def is_sentinel(value: int) -> bool:
    """True when the controller means "no sensor fitted"."""
    return value in SENTINELS


class CtcModbusClient:
    """A single, serialised Modbus TCP connection to the controller."""

    def __init__(self, host: str, port: int = 502, slave: int = 1) -> None:
        self._host = host
        self._port = port
        self._slave = slave
        self._client: Any = None
        self._lock = asyncio.Lock()
        self._last_request = 0.0

    async def _ensure_client(self) -> Any:
        if self._client is not None and getattr(self._client, "connected", False):
            return self._client
        try:
            from pymodbus.client import AsyncModbusTcpClient
        except ImportError as err:  # pragma: no cover - dependency is declared
            raise CtcModbusError("pymodbus is not available") from err

        self._client = AsyncModbusTcpClient(
            self._host, port=self._port, timeout=REQUEST_TIMEOUT
        )
        try:
            await asyncio.wait_for(self._client.connect(), timeout=CONNECT_TIMEOUT)
        except (asyncio.TimeoutError, OSError) as err:
            raise CtcModbusError(f"could not connect to {self._host}:{self._port}") from err
        if not getattr(self._client, "connected", False):
            raise CtcModbusError(f"could not connect to {self._host}:{self._port}")
        await asyncio.sleep(CONNECT_DELAY)
        self._last_request = time.monotonic()
        return self._client

    async def _pace(self) -> None:
        """Hold the documented gap between two transactions."""
        gap = MESSAGE_WAIT - (time.monotonic() - self._last_request)
        if gap > 0:
            await asyncio.sleep(gap)

    async def async_close(self) -> None:
        async with self._lock:
            if self._client is not None:
                close = getattr(self._client, "close", None)
                if close is not None:
                    result = close()
                    if asyncio.iscoroutine(result):
                        await result
                self._client = None

    def _slave_kwargs(self, client: Any, method: str) -> dict[str, int]:
        """pymodbus renamed the unit argument; support both spellings."""
        import inspect

        try:
            params = inspect.signature(getattr(client, method)).parameters
        except (TypeError, ValueError):  # pragma: no cover
            return {"slave": self._slave}
        if "device_id" in params:
            return {"device_id": self._slave}
        return {"slave": self._slave}

    async def async_read(self, address: int, count: int = 1) -> list[int]:
        """Read holding registers, splitting anything over the block limit."""
        if count < 1:
            return []
        out: list[int] = []
        async with self._lock:
            client = await self._ensure_client()
            kwargs = self._slave_kwargs(client, "read_holding_registers")
            offset = 0
            while offset < count:
                chunk = min(MAX_BLOCK, count - offset)
                await self._pace()
                try:
                    result = await asyncio.wait_for(
                        client.read_holding_registers(
                            address + offset, count=chunk, **kwargs
                        ),
                        timeout=REQUEST_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    # Registers the model does not implement are answered with
                    # silence rather than an exception code.
                    raise CtcModbusError(
                        f"register {address + offset} timed out, it may not exist on this model"
                    ) from None
                except Exception as err:  # noqa: BLE001 - pymodbus raises broadly
                    raise CtcModbusError(f"read of {address + offset} failed: {err}") from err
                if result is None or getattr(result, "isError", lambda: True)():
                    raise CtcModbusError(f"read of {address + offset} returned an error")
                self._last_request = time.monotonic()
                out.extend(result.registers)
                offset += chunk
        return out

    async def async_read_one(self, address: int, count: int = 1) -> list[int] | None:
        """Read, returning None instead of raising when the register is absent."""
        try:
            return await self.async_read(address, count)
        except CtcModbusError as err:
            _LOGGER.debug("%s", err)
            return None

    async def async_write(self, address: int, value: int) -> None:
        """Write one holding register with function code 16, as CTC specifies."""
        async with self._lock:
            client = await self._ensure_client()
            kwargs = self._slave_kwargs(client, "write_registers")
            raw = value & 0xFFFF if value >= 0 else (value + 65536) & 0xFFFF
            await self._pace()
            try:
                result = await asyncio.wait_for(
                    client.write_registers(address, [raw], **kwargs),
                    timeout=REQUEST_TIMEOUT,
                )
            except asyncio.TimeoutError as err:
                raise CtcModbusError(f"write to {address} timed out") from err
            except Exception as err:  # noqa: BLE001
                raise CtcModbusError(f"write to {address} failed: {err}") from err
            self._last_request = time.monotonic()
            if result is None or getattr(result, "isError", lambda: True)():
                raise CtcModbusError(f"write to {address} returned an error")

    async def async_probe(self) -> bool:
        """Confirm the controller answers on the documented outdoor register."""
        values = await self.async_read(62000, 1)
        return bool(values)


# Registers closer together than this are fetched in one transaction. Reading a
# few unused registers costs nothing; a separate round trip costs a lot, and the
# controller only allows one master.
BLOCK_GAP = 16


def plan_blocks(sensors: tuple[ModbusSensor, ...]) -> list[tuple[int, int]]:
    """Group register addresses into as few reads as the block limit allows."""
    if not sensors:
        return []
    wanted = sorted({(s.address, s.count) for s in sensors})
    blocks: list[tuple[int, int]] = []
    start = wanted[0][0]
    end = wanted[0][0] + wanted[0][1]
    for address, count in wanted[1:]:
        finish = address + count
        if address - end <= BLOCK_GAP and finish - start <= MAX_BLOCK:
            end = max(end, finish)
        else:
            blocks.append((start, end - start))
            start, end = address, finish
    blocks.append((start, end - start))
    return blocks
