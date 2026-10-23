from __future__ import annotations

from typing import Protocol

from tablink.paths.base import Path


class Service(Protocol):
    name: str
    needs_video: bool

    async def on_path_changed(self, path: Path | None) -> None: ...
