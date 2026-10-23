import asyncio
import json
from typing import Any

from tablink.adb import Adb
from tablink.control.server import ControlServer
from tablink.daemon import Daemon
from tablink.paths.manager import PathManager
from tablink.paths.usb import UsbPath
from tests.fakes.fake_adb import USB_SERIAL, FakeAdb


async def talk(port: int, *msgs: object) -> list[dict[str, Any]]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    replies = []
    for m in msgs:
        writer.write((m if isinstance(m, bytes) else json.dumps(m).encode()) + b"\n")
        await writer.drain()
        replies.append(json.loads(await reader.readline()))
    writer.close()
    return replies


async def test_hello_and_actions() -> None:
    calls: list[str] = []

    async def remote(msg: dict[str, Any]) -> dict[str, Any]:
        calls.append("remote")
        return {"type": "remote", "ok": True, "message": "opening"}

    srv = ControlServer(0, {"remote_ready": remote})
    await srv.start()
    try:
        hello, rem, bad, junk = await talk(
            srv.bound_port, {"type": "hello"}, {"type": "remote_ready"}, {"type": "nope"}, b"{x"
        )
    finally:
        await srv.stop()
    assert hello["type"] == "hello" and hello["version"]
    assert rem == {"type": "remote", "ok": True, "message": "opening"}
    assert bad["type"] == "error" and junk["type"] == "error"
    assert calls == ["remote"]


async def test_listens_on_loopback_only() -> None:
    srv = ControlServer(0, {})
    await srv.start()
    try:
        assert srv._server is not None
        assert {s.getsockname()[0] for s in srv._server.sockets} == {"127.0.0.1"}
    finally:
        await srv.stop()


async def test_handler_error_is_reported_not_fatal() -> None:
    async def boom(msg: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("adb exploded")

    srv = ControlServer(0, {"screen": boom})
    await srv.start()
    try:
        r1, r2 = await talk(srv.bound_port, {"type": "screen"}, {"type": "hello"})
    finally:
        await srv.stop()
    assert r1 == {"type": "screen", "ok": False, "message": "adb exploded"}
    assert r2["type"] == "hello"


async def test_second_request_while_busy_is_refused() -> None:
    gate = asyncio.Event()

    async def slow(msg: dict[str, Any]) -> dict[str, Any]:
        await gate.wait()
        return {"type": "remote_ready", "ok": True}

    srv = ControlServer(0, {"remote_ready": slow})
    first = asyncio.create_task(srv.dispatch({"type": "remote_ready"}))
    await asyncio.sleep(0)
    second = await srv.dispatch({"type": "remote_ready"})
    gate.set()
    assert second["ok"] is False and "Busy" in second["message"]
    assert (await first)["ok"] is True


async def test_daemon_follows_usb_plug_and_unplug() -> None:
    fake = FakeAdb(usb_attached=False)
    mgr = PathManager([UsbPath(Adb(runner=fake, adb_path="adb"), 47800)])
    d = Daemon(manager=mgr)
    await mgr.check()
    d.refresh_status()
    assert d.status.mode is None
    fake.usb_attached = True
    await mgr.check()
    d.refresh_status()
    assert d.status.mode == "usb" and d.status.serial == USB_SERIAL
    assert fake.reverses[USB_SERIAL][47800] == 47800
    fake.usb_attached = False
    await mgr.check()
    d.refresh_status()
    assert d.status.mode is None


async def test_daemon_local_status_and_unknown_command() -> None:
    from tablink import daemon as daemon_mod

    d = Daemon()
    await d.start()
    try:
        st = await daemon_mod.get_status()
        bad = await daemon_mod.request({"cmd": "format_disk"})
    finally:
        await d.stop()
    assert st.running and "error" in bad


async def test_hello_reports_active_mode() -> None:
    srv = ControlServer(0, {}, hello_extra=lambda: {"mode": "lan"})
    reply = await srv.dispatch({"type": "hello"})
    assert reply["mode"] == "lan" and reply["app"] == "tablink-laptop"
