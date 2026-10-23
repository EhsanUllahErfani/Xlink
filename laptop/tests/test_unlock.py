import pytest

from tablink.adb import Adb
from tablink.unlock import (
    Grid,
    UnlockError,
    parse_lock_screen,
    pattern_script,
    unlock_with_pattern,
    unlock_with_pin,
    valid_pattern,
)
from tests.fakes.fake_adb import USB_SERIAL, FakeAdb

# The real HyperOS 3 lock screen on the Redmi Pad 2 (portrait), trimmed.
PATTERN_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><hierarchy rotation="0">
<node resource-id="com.android.systemui:id/keyguard_pattern_view"
 class="android.widget.LinearLayout" package="com.android.systemui" bounds="[0,27][1600,2524]" />
<node resource-id="com.android.systemui:id/lockPatternView" class="android.view.View"
 package="com.android.systemui" bounds="[376,907][1224,1755]" />
</hierarchy>"""
PIN_XML = PATTERN_XML.replace("lockPatternView", "pinEntry")


def test_dots_are_cell_centres_of_the_real_grid() -> None:
    g = Grid(376, 907, 1224, 1755)
    assert g.dot(1) == (517, 1048)
    assert g.dot(5) == (800, 1331)
    assert g.dot(9) == (1082, 1613)


def test_parse_lock_screen_kinds() -> None:
    s = parse_lock_screen(PATTERN_XML)
    assert s.kind == "pattern" and s.grid == Grid(376, 907, 1224, 1755)
    assert parse_lock_screen(PIN_XML).kind == "pin"
    assert parse_lock_screen("").kind == "none"
    assert parse_lock_screen("<bad").kind == "none"


def test_pattern_rules() -> None:
    assert valid_pattern([1, 2, 3, 6])
    assert not valid_pattern([1, 2, 3])  # too short
    assert not valid_pattern([1, 2, 1, 4])  # repeated dot
    assert not valid_pattern([0, 1, 2, 3])


def test_script_is_one_stroke() -> None:
    script = pattern_script(Grid(0, 0, 300, 300), [1, 2, 3, 6])
    assert script.startswith("input motionevent DOWN 50 50;")
    assert script.count("MOVE") == 3 and script.endswith("input motionevent UP 250 150")


async def test_unlock_with_pattern_draws_once_and_never_logs_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("DEBUG")
    fake = FakeAdb(awake=False, locked=True, ui_xml=PATTERN_XML)
    assert await unlock_with_pattern(Adb(runner=fake, adb_path="adb"), USB_SERIAL, [1, 5, 9, 6])
    assert len(fake.drawn) == 1 and "1331" in fake.drawn[0]
    assert "motionevent" not in caplog.text  # the stroke (= the pattern) is never logged


async def test_wrong_pattern_reports_still_locked() -> None:
    fake = FakeAdb(locked=True, ui_xml=PATTERN_XML, unlock_on_draw=False)
    assert not await unlock_with_pattern(Adb(runner=fake, adb_path="adb"), USB_SERIAL, [1, 2, 3, 4])


async def test_already_unlocked_does_nothing() -> None:
    fake = FakeAdb(locked=False, ui_xml="")
    assert await unlock_with_pattern(Adb(runner=fake, adb_path="adb"), USB_SERIAL, [1, 2, 3, 4])
    assert fake.drawn == []


async def test_pin_on_a_pattern_screen_is_refused() -> None:
    fake = FakeAdb(locked=True, ui_xml=PATTERN_XML)
    with pytest.raises(UnlockError, match="not asking for a PIN"):
        await unlock_with_pin(Adb(runner=fake, adb_path="adb"), USB_SERIAL, "1234")
    assert fake.typed == []


async def test_unlock_with_pin(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")
    fake = FakeAdb(locked=True, ui_xml=PIN_XML)
    assert await unlock_with_pin(Adb(runner=fake, adb_path="adb"), USB_SERIAL, "482913")
    assert fake.typed == ["482913"] and "482913" not in caplog.text
