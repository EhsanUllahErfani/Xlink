"""The service tunnel: laptop services appear on the tablet's 127.0.0.1 via `adb reverse`.

`adb reverse` works over any adb transport (USB, or adb over TCP on Wi-Fi, hotspots,
Tailscale, Bluetooth PAN), so the tablet app and aFreeRDP always dial 127.0.0.1 and never need
to know which link is active.
"""

from __future__ import annotations

import asyncio
import logging

from tablink.adb import Adb, AdbError
from tablink.ports import PortMap

log = logging.getLogger(__name__)


class Tunnel:
    def __init__(self, adb: Adb, ports: list[PortMap]) -> None:
        self.adb = adb
        self.ports = ports
        self.serial: str | None = None

    async def open(self, serial: str) -> None:
        for p in self.ports:
            await self._reverse_with_retry(serial, p)
        self.serial = serial

    async def _reverse_with_retry(self, serial: str, p: PortMap, attempts: int = 4) -> None:
        # Right after adbd restarts the device is listed before its transport is usable.
        for i in range(attempts):
            try:
                await self.adb.reverse(serial, p.tablet, p.laptop)
                return
            except AdbError:
                if i == attempts - 1:
                    raise
                await asyncio.sleep(0.75)

    async def close(self) -> None:
        serial, self.serial = self.serial, None
        if serial is None:
            return
        for p in self.ports:
            try:
                await self.adb.reverse_remove(serial, p.tablet)
            except AdbError as e:  # the link may already be gone
                log.debug("reverse remove %s: %s", p.tablet, e)
