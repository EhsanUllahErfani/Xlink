"""Paths over an encrypted tailnet: TabLink's own network ("private", the laptop's server) or
Tailscale ("tailscale", mode 7 fallback).

Both ends run TabLink's built-in node. The laptop helper relays 127.0.0.1:<local port> to the
tablet's node on port 5555, which hands it to the tablet's adb. The tablet app reaches the laptop
through its own node (no adb reverse). WireGuard-encrypted end to end.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from tablink.adb import LEGACY_TCP_PORT, Adb, AdbError
from tablink.paths.base import PRIORITY, Path
from tablink.ports import CONTROL
from tablink.tailscale import Tailscale, find_tablet, ipv4

log = logging.getLogger(__name__)

LOCAL_ADB_PORT = 15555  # on 127.0.0.1 only


class TailscalePath:
    def __init__(
        self,
        adb: Adb,
        tailscale: Tailscale,
        enabled: Callable[[], bool],
        name_hint: Callable[[], str] = lambda: "",
        control_port: int = CONTROL,
        mode: str = "tailscale",
        local_port: int = LOCAL_ADB_PORT,
    ) -> None:
        self.mode = mode
        self.priority = PRIORITY[mode]
        self.local_port = local_port
        self.adb = adb
        self.tailscale = tailscale
        self.enabled = enabled
        self.name_hint = name_hint

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
        local = f"127.0.0.1:{self.local_port}"
        if not await self.tailscale.forward(local, f"{tablet}:{LEGACY_TCP_PORT}"):
            return None
        try:
            async with asyncio.timeout(15):
                serial = await self.adb.connect("127.0.0.1", self.local_port)
                start = time.monotonic()
                await self.adb.shell(serial, "echo", "ok")
                rtt = (time.monotonic() - start) * 1000
        except (AdbError, TimeoutError) as e:
            log.debug("tailscale probe: %s", e)
            return None
        return Path(self.mode, tablet, laptop, serial, rtt, video_ok=True)  # type: ignore[arg-type]

    # The tablet app reaches the laptop through its own node (no adb reverse tunnel).
    async def activate(self, path: Path) -> None:
        return None

    async def deactivate(self) -> None:
        return None
