"""F4 laptop side: files shared from the tablet land in ~/Downloads/TabLink."""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Callable
from pathlib import Path

CHUNK = 1 << 20


def downloads_dir() -> Path:
    return Path.home() / "Downloads" / "TabLink"


def safe_name(name: str) -> str:
    """A plain file name: no folders, no hidden files, nothing odd."""
    base = os.path.basename(name.replace("\\", "/")).strip()
    base = re.sub(r"[\x00-\x1f/]", "_", base).lstrip(".")
    return base[:200] or "file"


def unique_path(folder: Path, name: str) -> Path:
    p = folder / name
    stem, suffix = p.stem, p.suffix
    n = 1
    while p.exists():
        p = folder / f"{stem} ({n}){suffix}"
        n += 1
    return p


class FileReceiver:
    def __init__(
        self, folder: Path | None = None, on_saved: Callable[[str], None] | None = None
    ) -> None:
        self.folder = folder or downloads_dir()
        self.on_saved = on_saved

    async def receive(self, name: str, size: int, reader: asyncio.StreamReader) -> str:
        self.folder.mkdir(parents=True, exist_ok=True)
        dest = unique_path(self.folder, safe_name(name))
        tmp = dest.with_name(dest.name + ".part")
        try:
            with open(tmp, "wb") as f:  # noqa: ASYNC230 - chunks are written as they arrive
                left = size
                while left:
                    chunk = await reader.readexactly(min(CHUNK, left))
                    f.write(chunk)
                    left -= len(chunk)
            tmp.replace(dest)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        if self.on_saved:
            await asyncio.to_thread(self.on_saved, dest.name)
        return dest.name
