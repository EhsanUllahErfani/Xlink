import pytest

from tablink.adb import Adb, parse_focused_window
from tablink.remote_session import (
    AFREERDP,
    SESSION_ACTIVITY,
    FocusLost,
    find_login_dialog,
    open_laptop,
    session_link,
)
from tests.fakes.fake_adb import USB_SERIAL, FakeAdb

RDP = (AFREERDP, SESSION_ACTIVITY)
TIKTOK = ("com.zhiliaoapp.musically", "com.ss.android.ugc.aweme.main.MainActivity")

DIALOG = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><hierarchy rotation="0">
<node class="android.widget.FrameLayout" package="com.freerdp.afreerdp" bounds="[0,0][1600,2560]">
<node text="tablink" class="android.widget.EditText" package="com.freerdp.afreerdp"
  password="false" bounds="[196,1120][1394,1180]" />
<node text="" class="android.widget.EditText" package="com.freerdp.afreerdp"
  password="true" bounds="[196,1278][1394,1338]" />
<node text="" class="android.widget.EditText" package="com.freerdp.afreerdp"
  password="false" bounds="[196,1436][1394,1496]" />
<node text="CANCEL" class="android.widget.Button" package="com.freerdp.afreerdp"
  bounds="[1050,1550][1240,1620]" />
<node text="OK" class="android.widget.Button" package="com.freerdp.afreerdp"
  bounds="[1250,1550][1360,1620]" />
</node></hierarchy>"""

PW = "Ab3" * 8


def test_parse_focused_window_skips_null_displays() -> None:
    text = (
        "  mCurrentFocus=null\n"
        "  mCurrentFocus=Window{eaf5231 u0 com.miui.home/com.miui.home.launcher.Launcher}\n"
    )
    assert parse_focused_window(text) == ("com.miui.home", "com.miui.home.launcher.Launcher")
    assert parse_focused_window("  mCurrentFocus=Window{1 u0 a.b/.Main}") == ("a.b", "a.b.Main")
    assert parse_focused_window("mCurrentFocus=null") is None


def test_find_login_dialog() -> None:
    d = find_login_dialog(DIALOG)
    assert d is not None
    assert d.password_field.center == (795, 1308)
    assert d.ok_button.center == (1305, 1585)
    assert find_login_dialog("") is None
    assert find_login_dialog("<broken") is None


def test_session_link() -> None:
    assert session_link("127.0.0.1", 3389, "tablink") == "freerdp://tablink@127.0.0.1:3389"
    assert session_link("fd7a::1", 3389, "u") == "freerdp://u@[fd7a::1]:3389"


async def test_signs_in_when_dialog_is_in_front() -> None:
    fake = FakeAdb(focus=[RDP], ui_xml=DIALOG)
    adb = Adb(runner=fake, adb_path="adb")
    result = await open_laptop(adb, USB_SERIAL, "127.0.0.1", 3389, "tablink", PW, poll_s=0)
    assert result == "signed-in"
    assert fake.typed == [PW]
    assert fake.taps == [(795, 1308), (1305, 1585)]


async def test_never_types_after_user_switches_app() -> None:
    # Focus is aFreeRDP while we find the dialog, then the user opens TikTok.
    fake = FakeAdb(focus=[RDP, TIKTOK], ui_xml=DIALOG)
    adb = Adb(runner=fake, adb_path="adb")
    with pytest.raises(FocusLost):
        await open_laptop(adb, USB_SERIAL, "127.0.0.1", 3389, "tablink", PW, poll_s=0)
    assert fake.typed == [] and fake.taps == []


async def test_switch_right_before_typing_stops(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")
    # Found the dialog and tapped the field, then TikTok came to the front.
    fake = FakeAdb(focus=[RDP, RDP, TIKTOK], ui_xml=DIALOG)
    adb = Adb(runner=fake, adb_path="adb")
    with pytest.raises(FocusLost):
        await open_laptop(adb, USB_SERIAL, "127.0.0.1", 3389, "tablink", PW, poll_s=0)
    assert fake.typed == [] and len(fake.taps) == 1
    assert PW not in caplog.text


async def test_password_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")
    fake = FakeAdb(focus=[RDP], ui_xml=DIALOG)
    adb = Adb(runner=fake, adb_path="adb")
    await open_laptop(adb, USB_SERIAL, "127.0.0.1", 3389, "tablink", PW, poll_s=0)
    assert PW not in caplog.text


async def test_gives_up_if_rdp_never_shows() -> None:
    fake = FakeAdb(focus=[TIKTOK])
    adb = Adb(runner=fake, adb_path="adb")
    with pytest.raises(FocusLost):
        await open_laptop(adb, USB_SERIAL, "h", 3389, "u", PW, wait_s=0.05, poll_s=0.01)
    assert fake.typed == []


async def test_type_secret_rejects_shell_chars() -> None:
    adb = Adb(runner=FakeAdb(), adb_path="adb")
    with pytest.raises(ValueError):
        await adb.type_secret(USB_SERIAL, "a;reboot")
