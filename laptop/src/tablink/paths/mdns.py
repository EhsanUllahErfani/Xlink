"""Find the tablet on the local network by mDNS: TabLink announces `_tablink._tcp`.

Used by the Wi-Fi path when the tablet's address changed since it was last seen over USB.
Campus networks often block multicast; then known addresses and the gateway still apply.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from typing import Any

log = logging.getLogger(__name__)

SERVICE = "_tablink._tcp.local."


class MdnsDiscovery:
    def __init__(self) -> None:
        self.addrs: dict[str, list[str]] = {}  # service name -> IPv4 addresses
        self._zc: Any = None
        self._browser: Any = None
        self._tasks: set[asyncio.Future[None]] = set()

    def found(self) -> list[str]:
        return [a for addrs in self.addrs.values() for a in addrs]

    async def _resolve(self, zc: Any, name: str) -> None:
        from zeroconf.asyncio import AsyncServiceInfo

        info = AsyncServiceInfo(SERVICE, name)
        if await info.async_request(zc, 3000):
            self.addrs[name] = [socket.inet_ntoa(a) for a in info.addresses if len(a) == 4]

    def _on_change(self, zeroconf: Any, service_type: str, name: str, state_change: Any) -> None:
        from zeroconf import ServiceStateChange

        if state_change is ServiceStateChange.Removed:
            self.addrs.pop(name, None)
        else:
            task = asyncio.ensure_future(self._resolve(zeroconf, name))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    def start(self) -> None:
        try:
            from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf

            self._zc = AsyncZeroconf()
            self._browser = AsyncServiceBrowser(
                self._zc.zeroconf, SERVICE, handlers=[self._on_change]
            )
        except Exception as e:  # no multicast here: discovery is optional
            log.info("mDNS discovery unavailable: %s", type(e).__name__)

    async def stop(self) -> None:
        with contextlib.suppress(Exception):
            if self._browser is not None:
                await self._browser.async_cancel()
            if self._zc is not None:
                await self._zc.async_close()
