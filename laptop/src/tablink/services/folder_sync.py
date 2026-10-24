"""F5: the shared TabLink folder, ~/TabLink on the laptop and Documents/TabLink on the tablet.

Built into TabLink (no Syncthing app on the tablet): the daemon compares both sides every few
seconds over the active adb link (USB, Wi-Fi, hotspot or internet) and copies what changed.

Rules (docs/SPEC.md, F5):
- a file changed on one side is copied to the other;
- changed on both sides since the last sync: both versions are kept, the tablet's one renamed
  "name (conflict tablet YYYY-MM-DD HHMM).ext"; nothing is overwritten;
- deleted on one side and unchanged on the other: deleted there too, but recoverably (laptop:
  the desktop Trash; tablet: Documents/TabLink/.trash).
Hidden files and folders (starting with ".") are not synced.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Entry:
    size: int
    mtime: int  # seconds


Listing = dict[str, Entry]  # relative path (with "/") -> entry


class Remote(Protocol):
    """The tablet's side of the folder."""

    async def list(self) -> Listing: ...

    async def pull(self, rel: str, dest: Path) -> None: ...

    async def push(self, src: Path, rel: str) -> None: ...

    async def trash(self, rel: str) -> None: ...

    async def sha256(self, rel: str) -> str: ...


def hidden(rel: str) -> bool:
    return any(part.startswith(".") for part in rel.split("/"))


def list_local(root: Path) -> Listing:
    out: Listing = {}
    if not root.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if name.startswith(".") or name.endswith(".part"):
                continue
            p = Path(dirpath) / name
            try:
                st = p.stat()
            except OSError:
                continue
            out[p.relative_to(root).as_posix()] = Entry(st.st_size, int(st.st_mtime))
    return out


def conflict_name(rel: str, side: str, when: float) -> str:
    stamp = time.strftime("%Y-%m-%d %H%M", time.localtime(when))
    p = Path(rel)
    return str(p.with_name(f"{p.stem} (conflict {side} {stamp}){p.suffix}"))


def trash_local(path: Path) -> None:
    """Move to the desktop Trash (recoverable); fall back to a hidden .trash folder."""
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio

        Gio.File.new_for_path(str(path)).trash(None)
        return
    except Exception:
        pass
    root_trash = path.parent / ".trash"
    root_trash.mkdir(exist_ok=True)
    path.replace(root_trash / path.name)


@dataclass
class Plan:
    push: list[str]
    pull: list[str]
    trash_local: list[str]
    trash_remote: list[str]
    conflicts: list[str]

    def empty(self) -> bool:
        return not (
            self.push or self.pull or self.trash_local or self.trash_remote or self.conflicts
        )


def sha256_file(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def plan(local: Listing, remote: Listing, base: dict[str, dict[str, Entry]]) -> Plan:
    """Decide what to do. `base` = both sides' entries as of the last sync, per path."""
    p = Plan([], [], [], [], [])
    for rel in sorted(set(local) | set(remote) | set(base)):
        if hidden(rel):
            continue
        lo, re_, b = local.get(rel), remote.get(rel), base.get(rel)
        lo_changed = lo != (b or {}).get("laptop")
        re_changed = re_ != (b or {}).get("tablet")
        if b is None:  # never synced
            if lo and re_:
                p.conflicts.append(rel)  # sync_once drops it if the contents match
            elif lo:
                p.push.append(rel)
            elif re_:
                p.pull.append(rel)
            continue
        if lo is None and re_ is None:
            continue
        if lo is None:  # deleted on the laptop
            if re_changed:
                p.pull.append(rel)  # but edited on the tablet: keep the edit
            else:
                p.trash_remote.append(rel)
            continue
        if re_ is None:  # deleted on the tablet
            if lo_changed:
                p.push.append(rel)
            else:
                p.trash_local.append(rel)
            continue
        if lo_changed and re_changed:
            p.conflicts.append(rel)
        elif lo_changed:
            p.push.append(rel)
        elif re_changed:
            p.pull.append(rel)
    return p


class FolderSync:
    name = "folder"
    needs_video = False

    def __init__(
        self,
        root: Path,
        state_file: Path,
        remote_for: Callable[[], Remote | None],
        interval_s: float = 5.0,
        notify: Callable[[str, str], None] | None = None,
    ) -> None:
        self.root = root
        self.state_file = state_file
        self.remote_for = remote_for
        self.interval_s = interval_s
        self.notify = notify
        self.state = "idle"  # "idle", "syncing", "offline", "error"
        self._task: asyncio.Task[None] | None = None

    def _load_base(self) -> dict[str, dict[str, Entry]]:
        try:
            raw = json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            return {}
        return {rel: {side: Entry(**e) for side, e in sides.items()} for rel, sides in raw.items()}

    def _save_base(self, base: dict[str, dict[str, Entry]]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        data = {rel: {s: asdict(e) for s, e in sides.items()} for rel, sides in base.items()}
        tmp.write_text(json.dumps(data))
        tmp.replace(self.state_file)

    async def sync_once(self) -> Plan | None:
        remote = self.remote_for()
        if remote is None:
            self.state = "offline"
            return None
        self.root.mkdir(parents=True, exist_ok=True)
        base = await asyncio.to_thread(self._load_base)
        local = await asyncio.to_thread(list_local, self.root)
        theirs = await remote.list()
        todo = plan(local, theirs, base)
        # Files present on both sides but never synced: identical contents are not conflicts.
        for rel in list(todo.conflicts):
            if rel not in base and local[rel].size == theirs[rel].size:
                mine = await asyncio.to_thread(sha256_file, self.root / rel)
                if mine == await remote.sha256(rel):
                    todo.conflicts.remove(rel)
        if todo.empty() and base.keys() == (local.keys() & theirs.keys()):
            self.state = "idle"
            return todo
        self.state = "syncing"
        now = time.time()
        for rel in todo.push:
            await remote.push(self.root / rel, rel)
        for rel in todo.pull:
            dest = self.root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            await remote.pull(rel, dest)
        for rel in todo.trash_local:
            await asyncio.to_thread(trash_local, self.root / rel)
        for rel in todo.trash_remote:
            await remote.trash(rel)
        for rel in todo.conflicts:
            # Keep both: the tablet's version becomes a conflict copy on both sides.
            copy = conflict_name(rel, "tablet", now)
            dest = self.root / copy
            dest.parent.mkdir(parents=True, exist_ok=True)
            await remote.pull(rel, dest)
            await remote.push(self.root / rel, rel)
            await remote.push(dest, copy)
            if self.notify:
                self.notify("Edited on both devices", f"Kept both versions of {Path(rel).name}")
        # Record what both sides look like now.
        local = await asyncio.to_thread(list_local, self.root)
        theirs = await remote.list()
        new_base = {
            rel: {"laptop": local[rel], "tablet": theirs[rel]}
            for rel in local.keys() & theirs.keys()
        }
        await asyncio.to_thread(self._save_base, new_base)
        self.state = "idle"
        return todo

    async def run(self) -> None:
        while True:
            try:
                await self.sync_once()
            except Exception as e:
                self.state = "error"
                log.warning("folder sync: %s", type(e).__name__)
            await asyncio.sleep(self.interval_s)

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task


TABLET_ROOT = "/sdcard/Documents/TabLink"


class AdbRemote:
    """The tablet's Documents/TabLink folder, over the active adb link."""

    def __init__(self, adb: object, serial: str, root: str = TABLET_ROOT) -> None:
        from tablink.adb import Adb

        assert isinstance(adb, Adb)
        self.adb: Adb = adb
        self.serial = serial
        self.root = root.rstrip("/")

    def _path(self, rel: str) -> str:
        if rel.startswith("/") or ".." in rel.split("/"):
            raise ValueError(f"unsafe path: {rel}")
        return f"{self.root}/{rel}"

    async def list(self) -> Listing:
        import shlex

        root = shlex.quote(self.root)
        out = await self.adb.shell(
            self.serial,
            f"mkdir -p {root} && cd {root} && "
            "find . -type f ! -path '*/.*' -exec stat -c '%s %Y %n' {} +",
            timeout=60.0,
        )
        listing: Listing = {}
        for line in out.splitlines():
            parts = line.split(" ", 2)
            if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                rel = parts[2].removeprefix("./")
                listing[rel] = Entry(int(parts[0]), int(parts[1]))
        return listing

    async def pull(self, rel: str, dest: Path) -> None:
        tmp = dest.with_name(dest.name + ".part")
        await self.adb.pull(self.serial, self._path(rel), tmp)
        tmp.replace(dest)

    async def push(self, src: Path, rel: str) -> None:
        await self.adb.push_to(self.serial, src, self._path(rel))

    async def trash(self, rel: str) -> None:
        import shlex

        name = f"{int(time.time())}-{Path(rel).name}"
        await self.adb.shell(
            self.serial,
            f"mkdir -p {shlex.quote(self.root + '/.trash')} && "
            f"mv {shlex.quote(self._path(rel))} {shlex.quote(self.root + '/.trash/' + name)}",
        )

    async def sha256(self, rel: str) -> str:
        import shlex

        out = await self.adb.shell(self.serial, f"sha256sum {shlex.quote(self._path(rel))}")
        return out.split()[0] if out.split() else ""
