"""The path manager: owns switching. Probes every provider, keeps the best working path.

Every `interval_s` it re-checks the active path and probes the better ones. Services are told
about each change through `on_path_changed`; they never choose a path themselves.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Sequence

from tablink.paths.base import Path, PathProvider
from tablink.services.base import Service

log = logging.getLogger(__name__)


class PathManager:
    def __init__(
        self,
        providers: Sequence[PathProvider],
        services: Sequence[Service] = (),
        interval_s: float = 5.0,
        forced_mode: str | None = None,
    ) -> None:
        self.providers = sorted(providers, key=lambda p: p.priority)
        self.services = list(services)
        self.interval_s = interval_s
        self.forced_mode = forced_mode
        self.active: Path | None = None
        self._active_provider: PathProvider | None = None
        self._task: asyncio.Task[None] | None = None
        self.changed = asyncio.Event()
        # One check at a time: the 5 s loop and on-demand checks (path --force, hotspot)
        # must never activate/deactivate providers concurrently.
        self._check_lock = asyncio.Lock()

    def _candidates(self) -> list[PathProvider]:
        if self.forced_mode:
            return [p for p in self.providers if p.mode == self.forced_mode]
        return self.providers

    async def check(self) -> Path | None:
        """One round: find the best working path and switch to it if it changed."""
        async with self._check_lock:
            return await self._check_locked()

    async def _check_locked(self) -> Path | None:
        best: tuple[PathProvider, Path] | None = None
        for prov in self._candidates():
            # Providers worse than the active one are only tried if the active one failed.
            if best is not None:
                break
            try:
                path = await prov.probe()
            except Exception:
                log.exception("probe %s failed", prov.mode)
                path = None
            if path is not None:
                best = (prov, path)
        await self._switch(best)
        return self.active

    async def _switch(self, best: tuple[PathProvider, Path] | None) -> None:
        new_prov, new_path = best if best else (None, None)
        if self.active is None and new_path is None:
            return  # still no path: nothing to announce
        same = (
            self.active is not None
            and new_path is not None
            and (self.active.mode, self.active.adb_serial, self.active.tablet_addr)
            == (new_path.mode, new_path.adb_serial, new_path.tablet_addr)
        )
        if same:
            self.active = new_path  # fresh rtt
            return
        if self._active_provider is not None and self._active_provider is not new_prov:
            with contextlib.suppress(Exception):
                await self._active_provider.deactivate()
        if new_prov is not None and new_path is not None:
            try:
                await new_prov.activate(new_path)
            except Exception:
                log.exception("activating %s failed", new_prov.mode)
                new_prov, new_path = None, None
        old = self.active
        if old is None and new_path is None:
            return  # activation failed and we had no path before: nothing changed
        self.active, self._active_provider = new_path, new_prov
        log.info(
            "path: %s -> %s",
            old.mode if old else "none",
            new_path.mode if new_path else "none",
        )
        for svc in self.services:
            try:
                await svc.on_path_changed(new_path)
            except Exception:
                log.exception("service %s failed on path change", svc.name)
        self.changed.set()
        self.changed.clear()

    async def run(self) -> None:
        while True:
            await self.check()
            await asyncio.sleep(self.interval_s)

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self._active_provider is not None:
            with contextlib.suppress(Exception):
                await self._active_provider.deactivate()
