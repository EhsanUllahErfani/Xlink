"""Mode 7: laptop and tablet on different networks, linked through the user's own tailnet.

adb runs over TCP 5555 on the tablet's tailnet address (WireGuard-encrypted end to end), and
the same `adb reverse` tunnel as on Wi-Fi carries the control channel and remote desktop.
Video uses the Low preset.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from tablink.adb import LEGACY_TCP_PORT, Adb, AdbError
from tablink.paths.base import PRIORITY, Path
from tablink.paths.lan import tcp_reachable
from tablink.paths.tunnel import Tunnel
from tablink.ports import CONTROL, usb_reverse_ports
from tablink.tailscale import Tailscale, find_tablet, ipv4

log = logging.getLogger(__name__)


class TailscalePath:
    mode = "tailscale"
    priority = PRIORITY["tailscale"]

    def __init__(
        self,
        adb: Adb,
        tailscale: Tailscale,
        enabled: Callable[[], bool],
        name_hint: Callable[[], str] = lambda: "",
        reachable: Callable[[str, int], Awaitable[bool]] = tcp_reachable,
        control_port: int = CONTROL,
    ) -> None:
        self.adb = adb
        self.tailscale = tailscale
        self.enabled = enabled
        self.name_hint = name_hint
        self.reachable = reachable
        self.tunnel = Tunnel(adb, usb_reverse_ports(control_port))

    async def probe(self) -> Path | None:
        if not self.enabled():
            return None
        status = await self.tailscale.status()
        if not status.running:
            return None
        peer = find_tablet(status, self.name_hint())
        tablet = ipv4(peer.ips) if peer else None
        laptop = ipv4(status.self_ips)
        if tablet is None or laptop is None:
            return None
        if not await self.reachable(tablet, LEGACY_TCP_PORT):
            return None
        try:
            async with asyncio.timeout(10):
                serial = await self.adb.connect(tablet, LEGACY_TCP_PORT)
                start = time.monotonic()
                await self.adb.shell(serial, "echo", "ok")
                rtt = (time.monotonic() - start) * 1000
        except (AdbError, TimeoutError) as e:
            log.debug("tailscale probe: %s", e)
            return None
        return Path("tailscale", tablet, laptop, serial, rtt, video_ok=True)

    async def activate(self, path: Path) -> None:
        assert path.adb_serial
        await self.tunnel.open(path.adb_serial)

    async def deactivate(self) -> None:
        await self.tunnel.close()
