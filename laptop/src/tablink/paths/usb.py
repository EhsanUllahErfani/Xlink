"""Mode 1: USB cable. Every laptop service is tunnelled to the tablet's 127.0.0.1."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from tablink.adb import LEGACY_TCP_PORT, Adb, AdbError
from tablink.paths.base import PRIORITY, Path
from tablink.ports import PortMap, usb_reverse_ports

log = logging.getLogger(__name__)


class UsbPath:
    mode = "usb"
    priority = PRIORITY["usb"]

    def __init__(
        self,
        adb: Adb,
        control_port: int,
        serial_hint: str = "",
        prepare_wifi: bool = False,
        on_wifi_addrs: Callable[[list[str]], None] | None = None,
    ) -> None:
        self.adb = adb
        self.prepare_wifi = prepare_wifi
        self.on_wifi_addrs = on_wifi_addrs
        self.ports: list[PortMap] = usb_reverse_ports(control_port)
        self.serial_hint = serial_hint  # the paired tablet's serial, preferred when set
        self._active_serial: str | None = None

    async def probe(self) -> Path | None:
        try:
            devices = [d for d in await self.adb.devices() if d.transport == "usb" and d.ready]
        except AdbError as e:
            log.debug("usb probe: %s", e)
            return None
        if not devices:
            return None
        dev = next((d for d in devices if d.serial == self.serial_hint), devices[0])
        start = time.monotonic()
        try:
            await self.adb.shell(dev.serial, "echo", "ok")
        except AdbError as e:
            log.debug("usb probe %s: %s", dev.serial, e)
            return None
        rtt = (time.monotonic() - start) * 1000
        return Path(
            mode="usb",
            tablet_addr="127.0.0.1",
            laptop_addr="127.0.0.1",
            adb_serial=dev.serial,
            rtt_ms=rtt,
            video_ok=True,
        )

    async def _prepare_wifi(self, serial: str) -> None:
        """Learn the tablet's Wi-Fi address and turn on adb over TCP for the Wi-Fi path."""
        try:
            addrs = await self.adb.wifi_addrs(serial)
            if addrs and self.on_wifi_addrs:
                self.on_wifi_addrs(addrs)
            if await self.adb.tcp_port(serial) != LEGACY_TCP_PORT:
                log.info("enabling adb over Wi-Fi (port %d)", LEGACY_TCP_PORT)
                await self.adb.tcpip(serial, LEGACY_TCP_PORT)
                # adbd restarts: the USB link drops for a moment, then comes back.
                if not await self.adb.wait_for(serial):
                    log.warning("tablet did not come back after enabling adb over Wi-Fi")
        except AdbError as e:
            log.warning("could not prepare Wi-Fi: %s", e)

    async def activate(self, path: Path) -> None:
        assert path.adb_serial
        if self.prepare_wifi:
            await self._prepare_wifi(path.adb_serial)
        for p in self.ports:
            await self._reverse_with_retry(path.adb_serial, p)
        self._active_serial = path.adb_serial

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

    async def deactivate(self) -> None:
        serial, self._active_serial = self._active_serial, None
        if serial is None:
            return
        for p in self.ports:
            try:
                await self.adb.reverse_remove(serial, p.tablet)
            except AdbError as e:  # the cable may already be gone
                log.debug("reverse remove %s: %s", p.tablet, e)
