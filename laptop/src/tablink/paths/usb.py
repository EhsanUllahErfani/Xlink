"""Mode 1: USB cable. Every laptop service is tunnelled to the tablet's 127.0.0.1."""

from __future__ import annotations

import logging
import time

from tablink.adb import Adb, AdbError
from tablink.paths.base import PRIORITY, Path
from tablink.ports import PortMap, usb_reverse_ports

log = logging.getLogger(__name__)


class UsbPath:
    mode = "usb"
    priority = PRIORITY["usb"]

    def __init__(self, adb: Adb, control_port: int, serial_hint: str = "") -> None:
        self.adb = adb
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

    async def activate(self, path: Path) -> None:
        assert path.adb_serial
        for p in self.ports:
            await self.adb.reverse(path.adb_serial, p.tablet, p.laptop)
        self._active_serial = path.adb_serial

    async def deactivate(self) -> None:
        serial, self._active_serial = self._active_serial, None
        if serial is None:
            return
        for p in self.ports:
            try:
                await self.adb.reverse_remove(serial, p.tablet)
            except AdbError as e:  # the cable may already be gone
                log.debug("reverse remove %s: %s", p.tablet, e)
