"""Modes 2-5: the tablet is reachable by IP (router, either hotspot, a phone's hotspot).

adb runs over TCP port 5555 ("adb tcpip"), switched on over USB once per tablet boot. The
tablet's addresses are learned while it is on USB and remembered in the config, so no IP is
ever typed or hard-coded.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from tablink.adb import LEGACY_TCP_PORT, Adb, AdbError
from tablink.paths.base import PRIORITY, Mode, Path
from tablink.paths.netinfo import (
    default_gateways,
    gateway_ifaces,
    is_bluetooth_iface,
    local_addr_towards,
    neighbours,
)
from tablink.paths.tunnel import Tunnel
from tablink.ports import CONTROL, usb_reverse_ports

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 3.0


def classify(
    tablet_addr: str,
    gateways: list[str],
    laptop_is_hotspot: bool,
    gateway_iface: str = "",
) -> Mode:
    if laptop_is_hotspot:
        return "laptop_hotspot"
    if tablet_addr in gateways:
        # The tablet shares its network: over Bluetooth (PAN) or as a Wi-Fi hotspot.
        return "bluetooth" if is_bluetooth_iface(gateway_iface) else "tablet_hotspot"
    return "lan"


async def tcp_reachable(addr: str, port: int, timeout: float = 1.5) -> bool:
    try:
        async with asyncio.timeout(timeout):
            _, w = await asyncio.open_connection(addr, port)
    except (OSError, TimeoutError):
        return False
    w.close()
    return True


class LanPath:
    mode = "lan"
    priority = PRIORITY["lan"]

    def __init__(
        self,
        adb: Adb,
        known_addrs: Callable[[], list[str]],
        hotspot_iface: Callable[[], Awaitable[str | None]] | None = None,
        discovered: Callable[[], list[str]] = lambda: [],
        reachable: Callable[[str, int], Awaitable[bool]] = tcp_reachable,
        gateways: Callable[[], list[str]] = default_gateways,
        gateway_iface: Callable[[str], str] = lambda gw: gateway_ifaces().get(gw, ""),
        local_addr: Callable[[str], str | None] = local_addr_towards,
        control_port: int = CONTROL,
    ) -> None:
        self.tunnel = Tunnel(adb, usb_reverse_ports(control_port))
        self.adb = adb
        self.reachable = reachable
        self.gateways = gateways
        self.gateway_iface = gateway_iface
        self.local_addr = local_addr
        self.known_addrs = known_addrs
        self.hotspot_iface = hotspot_iface
        self.discovered = discovered
        self.neighbours = neighbours

    async def probe(self) -> Path | None:
        gateways = self.gateways()
        iface = await self.hotspot_iface() if self.hotspot_iface else None
        clients = self.neighbours(iface) if iface else []
        # Laptop-hotspot clients first (the tablet is one of very few), then known addresses,
        # then what mDNS found, then the gateway (the tablet itself when it is the hotspot).
        candidates = list(
            dict.fromkeys([*clients, *self.known_addrs(), *self.discovered(), *gateways])
        )
        for addr in candidates:
            path = await self._probe_one(addr, gateways, addr in clients)
            if path is not None:
                return path
        return None

    async def _probe_one(self, addr: str, gateways: list[str], on_hotspot: bool) -> Path | None:
        if not await self.reachable(addr, LEGACY_TCP_PORT):
            return None
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT_S + 2):
                serial = await self.adb.connect(addr, LEGACY_TCP_PORT)
                start = time.monotonic()
                await self.adb.shell(serial, "echo", "ok")
                rtt = (time.monotonic() - start) * 1000
        except (AdbError, TimeoutError) as e:
            log.debug("lan probe %s: %s", addr, e)
            return None
        laptop = self.local_addr(addr)
        if laptop is None:
            return None
        mode = classify(addr, gateways, on_hotspot, self.gateway_iface(addr))
        return Path(
            mode=mode,
            tablet_addr=addr,
            laptop_addr=laptop,
            adb_serial=serial,
            rtt_ms=rtt,
            video_ok=mode != "bluetooth",  # Bluetooth is too slow for screen control
        )

    async def activate(self, path: Path) -> None:
        assert path.adb_serial
        await self.tunnel.open(path.adb_serial)

    async def deactivate(self) -> None:
        await self.tunnel.close()
