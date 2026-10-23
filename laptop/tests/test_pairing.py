from pathlib import Path

import pytest

from tablink import config, pairing
from tablink.adb import Adb
from tablink.pairing import APKS, Apk, PairError, Pairing, fetch_verified, pick_apks
from tests.fakes.fake_adb import USB_SERIAL, FakeAdb
from tests.fakes.fake_grdctl import FakeGrd


class Log:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def __call__(self, state: str, text: str) -> None:
        self.lines.append((state, text))


def make(fake: FakeAdb, tmp_path: Path) -> tuple[Pairing, Log]:
    from tablink.services.remote_desktop import RemoteDesktop

    grd = FakeGrd()
    rd = RemoteDesktop(runner=grd, tty=grd.tty, cert_dir=tmp_path / "grd")
    log = Log()

    async def fetch(apk: Apk) -> Path:
        p = tmp_path / apk.file
        p.write_bytes(b"apk")
        return p

    from tablink.secrets import MemoryStore

    pairing_ = Pairing(
        Adb(runner=fake, adb_path="adb"),
        log,
        rd=rd,
        fetch=fetch,
        token_store=MemoryStore(),
        extensions_dir=tmp_path / "ext",
        enable_extension=lambda uuid: True,
    )
    return pairing_, log


def test_no_third_party_apps_needed() -> None:
    assert pick_apks(["arm64-v8a"]) == [] and APKS == []


async def test_unauthorized_tablet_gets_clear_instruction(tmp_path: Path) -> None:
    p, _ = make(FakeAdb(authorized=False), tmp_path)
    with pytest.raises(PairError, match="Allow USB debugging"):
        await p.find_tablet()


async def test_missing_hyperos_switches_are_named(tmp_path: Path) -> None:
    fake = FakeAdb()
    fake.props["persist.security.adbinput"] = "0"
    p, _ = make(fake, tmp_path)
    with pytest.raises(PairError, match=r"USB debugging \(Security settings\)"):
        await p.check_switches(USB_SERIAL)


async def test_installs_tablink_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    apk = tmp_path / "app-debug.apk"
    apk.write_bytes(b"apk")
    monkeypatch.setattr(pairing, "tablink_apk", lambda: apk)
    fake = FakeAdb()
    p, _ = make(fake, tmp_path)
    await p.install_apps(USB_SERIAL)
    assert fake.installed == [str(apk)]


async def test_refused_install_explains(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    apk = tmp_path / "app-debug.apk"
    apk.write_bytes(b"apk")
    monkeypatch.setattr(pairing, "tablink_apk", lambda: apk)
    p, _ = make(FakeAdb(refuse_install=True), tmp_path)
    with pytest.raises(PairError, match="Install via USB"):
        await p.install_apps(USB_SERIAL)


async def test_wifi_step_remembers_tablet_and_enables_tcp(tmp_path: Path) -> None:
    fake = FakeAdb()
    p, _ = make(fake, tmp_path)
    await p.wifi(USB_SERIAL)
    cfg = config.load()
    assert cfg.network.known_tablet_addrs == ["192.0.2.20"]
    assert cfg.device.tablet_serial == USB_SERIAL and fake.tcpip_enabled


async def test_full_run_hands_password_to_tablink_via_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("DEBUG")
    monkeypatch.setattr(pairing, "tablink_apk", lambda: None)
    fake = FakeAdb()
    p, log = make(fake, tmp_path)
    await p.run()
    assert log.lines[-1][0] == "ok"
    pw = fake.app_files["dev.tablink/rdp-password.pending"].decode()
    assert len(pw) == 24
    token = fake.app_files["dev.tablink/control-token.pending"].decode()
    assert len(token) == 64 and p.token_store.get() == token  # type: ignore[attr-defined]
    assert (tmp_path / "ext" / "tablink@dev.tablink" / "extension.js").exists()
    assert all(pw not in " ".join(c) for c in fake.calls)  # never on a command line
    assert pw not in caplog.text and all(pw not in t for _, t in log.lines)


async def test_fetch_rejects_bad_checksum(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_download(url: str, dest: Path) -> None:
        dest.write_bytes(b"tampered")

    monkeypatch.setattr(pairing, "_download", fake_download)
    apk = Apk("org.example.app", 1, "org.example.app_1.apk", "0" * 64)
    with pytest.raises(PairError, match="checksum"):
        await fetch_verified(apk, tmp_path)
    assert not (tmp_path / apk.file).exists()
