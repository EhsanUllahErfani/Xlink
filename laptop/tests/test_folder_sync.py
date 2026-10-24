import hashlib
import os
from pathlib import Path

import pytest

from tablink.services import folder_sync as fs
from tablink.services.folder_sync import AdbRemote, Entry, FolderSync, plan


class MemoryRemote:
    def __init__(self) -> None:
        self.files: dict[str, tuple[bytes, int]] = {}
        self.trashed: list[str] = []
        self.clock = 1_000_000

    def put(self, rel: str, data: bytes) -> None:
        self.clock += 10
        self.files[rel] = (data, self.clock)

    async def list(self) -> dict[str, Entry]:
        return {r: Entry(len(d), m) for r, (d, m) in self.files.items() if not fs.hidden(r)}

    async def pull(self, rel: str, dest: Path) -> None:
        dest.write_bytes(self.files[rel][0])

    async def push(self, src: Path, rel: str) -> None:
        self.put(rel, src.read_bytes())

    async def trash(self, rel: str) -> None:
        self.trashed.append(rel)
        del self.files[rel]

    async def sha256(self, rel: str) -> str:
        return hashlib.sha256(self.files[rel][0]).hexdigest()


@pytest.fixture(autouse=True)
def no_desktop_trash(monkeypatch: pytest.MonkeyPatch) -> None:
    def to_hidden_trash(path: Path) -> None:
        (path.parent / ".trash").mkdir(exist_ok=True)
        path.replace(path.parent / ".trash" / path.name)

    monkeypatch.setattr(fs, "trash_local", to_hidden_trash)


def setup(tmp_path: Path) -> tuple[FolderSync, MemoryRemote, Path]:
    remote = MemoryRemote()
    root = tmp_path / "TabLink"
    sync = FolderSync(root, tmp_path / "state.json", lambda: remote)
    return sync, remote, root


def write(root: Path, rel: str, data: bytes, mtime: int) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    os.utime(p, (mtime, mtime))


async def test_new_files_flow_both_ways(tmp_path: Path) -> None:
    sync, remote, root = setup(tmp_path)
    root.mkdir()
    write(root, "notes/a.txt", b"from laptop", 100)
    remote.put("photo.jpg", b"from tablet")
    await sync.sync_once()
    assert remote.files["notes/a.txt"][0] == b"from laptop"
    assert (root / "photo.jpg").read_bytes() == b"from tablet"
    assert (await sync.sync_once()).empty()  # steady state: nothing to do


async def test_edit_on_one_side_is_copied(tmp_path: Path) -> None:
    sync, remote, root = setup(tmp_path)
    root.mkdir()
    write(root, "a.txt", b"v1", 100)
    await sync.sync_once()
    write(root, "a.txt", b"v2 laptop", 200)
    await sync.sync_once()
    assert remote.files["a.txt"][0] == b"v2 laptop"
    remote.put("a.txt", b"v3 tablet edit")
    await sync.sync_once()
    assert (root / "a.txt").read_bytes() == b"v3 tablet edit"


async def test_edit_on_both_sides_keeps_both(tmp_path: Path) -> None:
    notes: list[str] = []
    sync, remote, root = setup(tmp_path)
    sync.notify = lambda title, body: notes.append(body)
    root.mkdir()
    write(root, "report.txt", b"v1", 100)
    await sync.sync_once()
    write(root, "report.txt", b"laptop edit", 300)
    remote.put("report.txt", b"tablet edit")
    await sync.sync_once()
    copies = [r for r in remote.files if r.startswith("report (conflict tablet")]
    assert len(copies) == 1 and copies[0].endswith(".txt")
    assert remote.files["report.txt"][0] == b"laptop edit"
    assert remote.files[copies[0]][0] == b"tablet edit"
    assert (root / "report.txt").read_bytes() == b"laptop edit"
    assert (root / copies[0]).read_bytes() == b"tablet edit"  # identical on both sides
    assert notes and "report.txt" in notes[0]


async def test_deletes_are_recoverable(tmp_path: Path) -> None:
    sync, remote, root = setup(tmp_path)
    root.mkdir()
    write(root, "old.txt", b"x", 100)
    remote.put("gone.txt", b"y")
    await sync.sync_once()
    (root / "old.txt").unlink()
    del remote.files["gone.txt"]
    await sync.sync_once()
    assert remote.trashed == ["old.txt"]
    assert not (root / "gone.txt").exists() and (root / ".trash" / "gone.txt").exists()


async def test_delete_vs_edit_keeps_the_edit(tmp_path: Path) -> None:
    sync, remote, root = setup(tmp_path)
    root.mkdir()
    write(root, "a.txt", b"v1", 100)
    await sync.sync_once()
    (root / "a.txt").unlink()
    remote.put("a.txt", b"edited on tablet")
    await sync.sync_once()
    assert (root / "a.txt").read_bytes() == b"edited on tablet"


async def test_first_sync_same_file_is_not_a_conflict(tmp_path: Path) -> None:
    sync, remote, root = setup(tmp_path)
    root.mkdir()
    write(root, "same.txt", b"hello", 100)
    remote.put("same.txt", b"hello")
    write(root, "diff.txt", b"aaaaa", 100)
    remote.put("diff.txt", b"bbbbb")  # same size, different content
    await sync.sync_once()
    assert not any("same (conflict" in r for r in remote.files)
    assert any(r.startswith("diff (conflict tablet") for r in remote.files)


async def test_hidden_files_are_not_synced(tmp_path: Path) -> None:
    sync, remote, root = setup(tmp_path)
    root.mkdir()
    write(root, ".secret/key", b"k", 100)
    write(root, "doc.part", b"half", 100)
    await sync.sync_once()
    assert remote.files == {}


async def test_offline_without_link(tmp_path: Path) -> None:
    sync = FolderSync(tmp_path / "T", tmp_path / "s.json", lambda: None)
    assert await sync.sync_once() is None and sync.state == "offline"


def test_plan_only_touches_changed_paths() -> None:
    e1, e2 = Entry(1, 100), Entry(2, 200)
    base = {"a": {"laptop": e1, "tablet": e1}}
    assert plan({"a": e1}, {"a": e1}, base).empty()
    assert plan({"a": e2}, {"a": e1}, base).push == ["a"]


def test_remote_paths_cannot_escape_the_folder() -> None:
    from tablink.adb import Adb
    from tests.fakes.fake_adb import FakeAdb

    r = AdbRemote(Adb(runner=FakeAdb(), adb_path="adb"), "S")
    assert r._path("a/b.txt") == "/sdcard/Documents/TabLink/a/b.txt"
    for bad in ("../x", "/etc/passwd", "a/../../x"):
        with pytest.raises(ValueError):
            r._path(bad)
