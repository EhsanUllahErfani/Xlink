import asyncio
import io

import pytest

from tablink import cli, daemon
from tablink.daemon import Daemon, DaemonStatus, ServiceStatus


async def test_status_when_daemon_not_running() -> None:
    st = await daemon.get_status()
    assert st == DaemonStatus(running=False)


async def test_status_from_running_daemon() -> None:
    d = Daemon()
    d.status.mode = "usb"
    d.status.rtt_ms = 3.2
    d.status.services = [ServiceStatus("scrcpy", "connected")]
    await d.start()
    try:
        st = await daemon.get_status()
    finally:
        await d.stop()
    assert st.running and st.mode == "usb" and st.rtt_ms == 3.2
    assert st.services == [ServiceStatus("scrcpy", "connected")]
    assert not d.sock.exists()


async def test_daemon_rejects_unknown_command() -> None:
    d = Daemon()
    await d.start()
    try:
        reply = await daemon.request({"cmd": "rm -rf"})
    finally:
        await d.stop()
    assert "error" in reply


def test_cli_status_offline(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["status"]) == 3
    out = capsys.readouterr().out
    assert "not running" in out
    assert "Redmi Pad 2" in out


def test_cli_status_connected(capsys: pytest.CaptureFixture[str]) -> None:
    d = Daemon()
    d.status.mode = "lan"
    d.status.rtt_ms = 7.0
    d.status.services = [ServiceStatus("syncthing", "syncing", "2 files")]

    async def run() -> int:
        await d.start()
        try:
            return await asyncio.to_thread(cli.main, ["status"])
        finally:
            await d.stop()

    assert asyncio.run(run()) == 0
    out = capsys.readouterr().out
    assert "same network" in out and "7 ms" in out
    assert "syncthing" in out and "syncing" in out and "2 files" in out


def test_cli_status_bad_config(capsys: pytest.CaptureFixture[str]) -> None:
    from tablink import config

    p = config.config_path()
    p.parent.mkdir(parents=True)
    p.write_text("[network]\ncontrol_port = 0\n")
    assert cli.main(["status"]) == 1
    assert "bad config" in capsys.readouterr().err


def test_unimplemented_command_says_which_task(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["doctor"]) == 2
    assert "T18" in capsys.readouterr().err


def test_format_status_offline_link() -> None:
    from tablink.config import Config

    text = cli.format_status(DaemonStatus(running=True), Config(), cli.Style(io.StringIO()))
    assert "offline" in text
