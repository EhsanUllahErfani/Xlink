"""Mode 7: laptop and tablet on different networks, linked through the user's own tailnet.

Both ends run TabLink's built-in Tailscale node (no Tailscale install on either). The laptop
helper relays 127.0.0.1:LOCAL_ADB_PORT to the tablet's TabLink node on port 5555, which hands it
to the tablet's adb. adb therefore dials a local port, and the usual `adb reverse` tunnel carries
the control channel and remote desktop over it. WireGuard-encrypted end to end; Low preset.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from tablink.adb import LEGACY_TCP_PORT, Adb, AdbError
from tablink.paths.base import PRIORITY, Path
from tablink.paths.tunnel import Tunnel
from tablink.ports import CONTROL, usb_reverse_ports
from tablink.tailscale import Tailscale, find_tablet, ipv4

log = logging.getLogger(__name__)

LOCAL_ADB_PORT = 15555  # on 127.0.0.1 only


class TailscalePath:
    mode = "tailscale"
    priority = PRIORITY["tailscale"]

    def __init__(
        self,
        adb: Adb,
        tailscale: Tailscale,
        enabled: Callable[[], bool],
        name_hint: Callable[[], str] = lambda: "",
        control_port: int = CONTROL,
    ) -> None:
        self.adb = adb
        self.tailscale = tailscale
        self.enabled = enabled
        self.name_hint = name_hint
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
        local = f"127.0.0.1:{LOCAL_ADB_PORT}"
        if not await self.tailscale.forward(local, f"{tablet}:{LEGACY_TCP_PORT}"):
            return None
        try:
            async with asyncio.timeout(15):
                serial = await self.adb.connect("127.0.0.1", LOCAL_ADB_PORT)
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
