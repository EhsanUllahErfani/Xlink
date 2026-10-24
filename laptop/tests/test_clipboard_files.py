from pathlib import Path

import pytest

from tablink.services.clipboard import ClipboardSync
from tablink.services.files import safe_name, unique_path


class FakeBridge:
    def __init__(self, text: str | None = "old") -> None:
        self.text = text

    def get_text(self) -> str | None:
        return self.text

    def set_text(self, text: str) -> None:
        self.text = text


async def test_pushes_new_laptop_copies_only() -> None:
    pushed: list[str] = []

    async def push(t: str) -> None:
        pushed.append(t)

    bridge = FakeBridge()
    sync = ClipboardSync(bridge, push)
    await sync.check()  # whatever was there before TabLink started is not sent
    assert pushed == [] and sync.available
    bridge.text = "copied on laptop"
    await sync.check()
    await sync.check()
    assert pushed == ["copied on laptop"]


async def test_text_from_tablet_is_not_echoed_back() -> None:
    pushed: list[str] = []

    async def push(t: str) -> None:
        pushed.append(t)

    bridge = FakeBridge()
    sync = ClipboardSync(bridge, push)
    await sync.check()
    await sync.from_tablet("from tablet")
    assert bridge.text == "from tablet"
    await sync.check()
    assert pushed == []


async def test_missing_extension_is_reported_not_fatal() -> None:
    async def push(t: str) -> None:
        raise AssertionError

    sync = ClipboardSync(FakeBridge(None), push)
    await sync.check()
    assert not sync.available


@pytest.mark.parametrize(
    ("name", "safe"),
    [
        ("photo.jpg", "photo.jpg"),
        ("../../etc/passwd", "passwd"),
        ("C:\\\\Users\\\\x\\\\doc.pdf", "doc.pdf"),
        (".hidden", "hidden"),
        ("", "file"),
        ("a\nb.txt", "a_b.txt"),
    ],
)
def test_safe_name(name: str, safe: str) -> None:
    assert safe_name(name) == safe


def test_unique_path_never_overwrites(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("1")
    (tmp_path / "a (1).txt").write_text("2")
    assert unique_path(tmp_path, "a.txt").name == "a (2).txt"


async def test_from_tablet_without_extension_reports_false() -> None:
    class Missing(FakeBridge):
        def set_text(self, text: str) -> None:
            raise RuntimeError("ServiceUnknown")

    async def push(t: str) -> None:
        raise AssertionError

    sync = ClipboardSync(Missing(), push)
    assert await sync.from_tablet("x") is False


async def test_receiver_announces_saved_file(tmp_path: Path) -> None:
    import asyncio

    from tablink.services.files import FileReceiver

    seen: list[str] = []
    r = FileReceiver(tmp_path, on_saved=seen.append)
    reader = asyncio.StreamReader()
    reader.feed_data(b"hello")
    reader.feed_eof()
    assert await r.receive("note.txt", 5, reader) == "note.txt"
    assert seen == ["note.txt"]
