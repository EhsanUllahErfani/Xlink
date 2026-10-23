import asyncio
from dataclasses import replace

import pytest

from tablink.adb import Adb
from tablink.config import Config
from tablink.paths.base import Path
from tablink.services import scrcpy as scrcpy_mod
from tablink.services.scrcpy import ScrcpyService, build_argv, explain_failure
from tests.fakes.fake_adb import USB_SERIAL, FakeAdb
from tests.fakes.fake_scrcpy import FakeSpawner

USB = Path("usb", "127.0.0.1", "127.0.0.1", USB_SERIAL, 2.0, True)
LAN = Path("lan", "192.0.2.20", "192.0.2.5", "192.0.2.20:5555", 8.0, True)
BT = Path("bluetooth", "192.0.2.30", "192.0.2.31", None, 90.0, False)


@pytest.fixture
def spawner() -> FakeSpawner:
    return FakeSpawner()


@pytest.fixture
def svc(spawner: FakeSpawner) -> ScrcpyService:
    adb = Adb(runner=FakeAdb(connected_tcp=["192.0.2.20:5555"]), adb_path="/sdk/adb")
    return ScrcpyService(adb, Config().video, "Pad · TabLink", spawner, scrcpy_path="scrcpy")


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


def test_argv_uses_preset_and_drop_folder() -> None:
    argv = build_argv("scrcpy", "S1", Config().video["balanced"], "T")
    assert argv[0] == "scrcpy"
    for flag in (
        "--serial=S1",
        "--max-size=1600",
        "--video-bit-rate=8M",
        "--max-fps=60",
        "--window-title=T",
        "--push-target=/sdcard/Download/TabLink/",
    ):
        assert flag in argv


async def test_open_starts_window_with_path_preset(
    svc: ScrcpyService, spawner: FakeSpawner
) -> None:
    await svc.on_path_changed(USB)
    assert spawner.procs == []  # nothing until the user asks
    await svc.open()
    assert "--max-size=2560" in spawner.last.argv  # USB = high
    assert spawner.last.env == {"ADB": "/sdk/adb"}  # scrcpy uses the same adb
    assert svc.state == "connected"


async def test_user_closing_window_ends_session(svc: ScrcpyService, spawner: FakeSpawner) -> None:
    await svc.on_path_changed(USB)
    await svc.open()
    spawner.last.exit(0)
    end = await svc.wait_closed()
    assert end.by_user and end.reason == ""
    assert not svc.wanted and svc.state == "disabled"
    await svc.on_path_changed(LAN)  # a new path does not reopen a closed window
    assert len(spawner.procs) == 1


async def test_path_change_restarts_window_on_new_path(
    svc: ScrcpyService, spawner: FakeSpawner
) -> None:
    await svc.on_path_changed(USB)
    await svc.open()
    first = spawner.last
    await svc.on_path_changed(LAN)
    assert first.terminated
    assert "--serial=192.0.2.20:5555" in spawner.last.argv
    assert len(spawner.live) == 1


async def test_same_path_with_new_rtt_does_not_restart(
    svc: ScrcpyService, spawner: FakeSpawner
) -> None:
    await svc.on_path_changed(USB)
    await svc.open()
    await svc.on_path_changed(replace(USB, rtt_ms=5.0))
    assert len(spawner.procs) == 1


async def test_path_loss_closes_then_reopens_when_back(
    svc: ScrcpyService, spawner: FakeSpawner
) -> None:
    await svc.on_path_changed(USB)
    await svc.open()
    await svc.on_path_changed(None)
    assert spawner.live == [] and svc.state == "offline"
    assert "Waiting" in svc.status_detail
    await svc.on_path_changed(USB)
    assert len(spawner.live) == 1


async def test_bluetooth_disables_video_with_clear_message(
    svc: ScrcpyService, spawner: FakeSpawner
) -> None:
    await svc.on_path_changed(BT)
    await svc.open()
    assert spawner.procs == []
    assert "too slow" in svc.status_detail


async def test_crash_reports_reason(svc: ScrcpyService, spawner: FakeSpawner) -> None:
    await svc.on_path_changed(USB)
    await svc.open()
    spawner.last.exit(2, "ERROR: Device disconnected")
    end = await svc.wait_closed()
    assert not end.by_user and end.reason == "The tablet disconnected."


async def test_instant_failure_is_not_missed(spawner: FakeSpawner, svc: ScrcpyService) -> None:
    spawner.exit_immediately = (1, ("ERROR: Could not find any ADB device",))
    await svc.on_path_changed(USB)
    await svc.open()
    end = await asyncio.wait_for(svc.wait_closed(), 1)
    assert end.reason == "The tablet is not connected."


async def test_close_resolves_wait(svc: ScrcpyService, spawner: FakeSpawner) -> None:
    await svc.on_path_changed(USB)
    await svc.open()
    waiter = asyncio.create_task(svc.wait_closed())
    await settle()
    await svc.close()
    assert (await waiter).by_user
    assert spawner.last.terminated


@pytest.mark.parametrize(
    ("code", "lines", "expected"),
    [
        (2, [], "The tablet disconnected."),
        (1, ["ERROR: Device is unauthorized"], "Unlock the tablet"),
        (1, ["ERROR: Could not open video stream", "ERROR: encoder failed"], "Low video preset"),
        (1, ["ERROR: something odd"], "something odd"),
        (1, [], "exit 1"),
    ],
)
def test_explain_failure(code: int, lines: list[str], expected: str) -> None:
    assert expected in explain_failure(code, lines)


def test_find_scrcpy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TABLINK_SCRCPY", "/x/scrcpy")
    assert scrcpy_mod.find_scrcpy() == "/x/scrcpy"
