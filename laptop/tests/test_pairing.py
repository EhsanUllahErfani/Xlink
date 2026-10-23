from pathlib import Path

import pytest

from tablink import config, pairing
from tablink.adb import Adb
from tablink.afreerdp_setup import existing_bookmark, find, parse_nodes, row_for
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

    return Pairing(Adb(runner=fake, adb_path="adb"), log, rd=rd, fetch=fetch), log


def test_pick_apks_by_cpu() -> None:
    arm64 = pick_apks(["arm64-v8a", "armeabi-v7a"])
    assert {a.package: a.abi for a in arm64}["com.freerdp.afreerdp"] == "arm64-v8a"
    arm32 = pick_apks(["armeabi-v7a"])
    assert {a.package: a.abi for a in arm32}["com.freerdp.afreerdp"] == "armeabi-v7a"
    assert all(len(a.sha256) == 64 for a in APKS)


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


async def test_install_skips_up_to_date_apps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pairing, "tablink_apk", lambda: None)
    fake = FakeAdb(packages={"org.kde.kdeconnect_tp": 13600})
    p, log = make(fake, tmp_path)
    await p.install_apps(USB_SERIAL)
    assert [Path(x).name for x in fake.installed] == ["com.freerdp.afreerdp_3320103.apk"]
    assert ("skip", "org.kde.kdeconnect_tp is already installed") in log.lines


async def test_refused_install_explains(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pairing, "tablink_apk", lambda: None)
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


async def test_full_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pairing, "tablink_apk", lambda: None)

    async def fake_setup(self: object, host: str, port: int, user: str, pw: str) -> bool:
        assert (host, port, user) == ("127.0.0.1", 3389, "tablink") and len(pw) == 24
        return True

    monkeypatch.setattr(pairing.AFreeRdpSetup, "run", fake_setup)
    p, log = make(FakeAdb(), tmp_path)
    await p.run()
    assert log.lines[-1][0] == "ok"
    assert config.load().remote.afreerdp_bookmark == 1
    assert all("tablink" not in t or len(t) < 200 for _, t in log.lines)


async def test_fetch_rejects_bad_checksum(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_download(url: str, dest: Path) -> None:
        dest.write_bytes(b"tampered")

    monkeypatch.setattr(pairing, "_download", fake_download)
    with pytest.raises(PairError, match="checksum"):
        await fetch_verified(APKS[0], tmp_path)
    assert not (tmp_path / APKS[0].file).exists()


HOME_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><hierarchy rotation="0">
<node text="" resource-id="com.freerdp.afreerdp:id/newBookmark" class="android.widget.Button"
 package="com.freerdp.afreerdp" content-desc="New Connection" bounds="[1150,88][1258,196]" />
<node text="Laptop" resource-id="com.freerdp.afreerdp:id/bookmark_text1"
 class="android.widget.TextView" package="com.freerdp.afreerdp" bounds="[118,226][1483,293]" />
<node text="Hostname" resource-id="com.freerdp.afreerdp:id/title" class="android.widget.TextView"
 package="com.freerdp.afreerdp" bounds="[180,529][349,578]" />
<node text="evil" resource-id="x:id/title" class="android.widget.TextView" package="other.app"
 bounds="[0,0][1,1]" />
</hierarchy>"""


def test_afreerdp_screen_parsing() -> None:
    nodes = parse_nodes(HOME_XML)
    assert len(nodes) == 3  # other apps' nodes are ignored
    assert existing_bookmark(nodes)
    new = find(nodes, rid="newBookmark")
    assert new and new.text == "New Connection" and new.box.center == (1204, 142)
    row = row_for(nodes, "Hostname")
    assert row and row.box.center == (264, 553)
    assert parse_nodes("") == [] and parse_nodes("<bad") == []
