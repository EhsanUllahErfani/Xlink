"""Async wrapper around `adb`. The only module that runs adb.

Every call goes through a `Runner`, so tests swap in `tests/fakes/fake_adb.py` and need no device.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from tablink.proc import Runner, RunResult, subprocess_runner

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 15.0
LEGACY_TCP_PORT = 5555


class AdbError(RuntimeError):
    pass


class AdbNotFound(AdbError):
    pass


@dataclass(frozen=True)
class Device:
    serial: str
    state: str  # "device", "unauthorized", "offline", ...
    transport: Literal["usb", "tcp"]
    model: str = ""
    product: str = ""

    @property
    def ready(self) -> bool:
        return self.state == "device"


def find_adb() -> str:
    """adb from $TABLINK_ADB, PATH, or the Android SDK."""
    if env := os.environ.get("TABLINK_ADB"):
        return env
    if found := shutil.which("adb"):
        return found
    for root in (os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT")):
        if root and (p := Path(root) / "platform-tools" / "adb").exists():
            return str(p)
    sdk = Path.home() / "Android" / "Sdk" / "platform-tools" / "adb"
    if sdk.exists():
        return str(sdk)
    raise AdbNotFound("adb not found: install android-tools-adb or the Android SDK platform-tools")


def parse_devices(text: str) -> list[Device]:
    """Parse `adb devices -l`."""
    devices = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("List of devices", "*")):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state, *rest = parts
        props = dict(p.split(":", 1) for p in rest if ":" in p)
        transport: Literal["usb", "tcp"] = "usb" if "usb" in props else "tcp"
        devices.append(
            Device(
                serial=serial,
                state=state,
                transport=transport,
                model=props.get("model", ""),
                product=props.get("product", ""),
            )
        )
    return devices


_IPV4 = re.compile(r"\binet (\d{1,3}(?:\.\d{1,3}){3})/\d+")


def parse_ipv4_addrs(text: str) -> list[str]:
    """Parse `ip -o -4 addr show ...` output into addresses, skipping loopback."""
    return [a for a in _IPV4.findall(text) if not a.startswith("127.")]


def _host_port(host: str, port: int) -> str:
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


class Adb:
    def __init__(self, runner: Runner | None = None, adb_path: str | None = None) -> None:
        self._runner = runner or subprocess_runner
        self._adb_path = adb_path

    @property
    def adb_path(self) -> str:
        if self._adb_path is None:
            self._adb_path = find_adb()
        return self._adb_path

    async def _run(
        self,
        *args: str,
        serial: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
        check: bool = True,
        quiet_args: bool = False,
        input: bytes | None = None,
    ) -> RunResult:
        argv = [self.adb_path]
        if serial:
            argv += ["-s", serial]
        argv += args
        log.debug("adb %s", "<hidden>" if quiet_args else " ".join(argv[1:]))
        try:
            res = await self._runner(argv, timeout, input=input)
        except FileNotFoundError as e:
            raise AdbNotFound(str(e)) from e
        except TimeoutError as e:
            raise AdbError(f"adb {args[0]} timed out after {timeout:.0f} s") from e
        if check and res.returncode != 0:
            msg = (res.stderr or res.stdout).strip() or f"exit code {res.returncode}"
            raise AdbError(f"adb {args[0]}: {msg}")
        return res

    async def devices(self) -> list[Device]:
        return parse_devices((await self._run("devices", "-l")).stdout)

    async def usb_device(self) -> Device | None:
        """The first authorised device attached by USB, if any."""
        for d in await self.devices():
            if d.transport == "usb" and d.ready:
                return d
        return None

    async def connect(self, host: str, port: int = LEGACY_TCP_PORT) -> str:
        """Connect over TCP and return the serial. Raises AdbError on failure."""
        target = _host_port(host, port)
        res = await self._run("connect", target, check=False, timeout=10.0)
        out = (res.stdout + res.stderr).strip()
        failed = re.search(r"cannot|failed|unable", out, re.IGNORECASE)
        if res.returncode == 0 and "connected to" in out and not failed:
            return target
        raise AdbError(f"adb connect {target}: {out or 'failed'}")

    async def disconnect(self, target: str) -> None:
        await self._run("disconnect", target, check=False)

    async def pair(self, host: str, port: int, code: str) -> None:
        """Wireless-debugging pairing. The code is never logged."""
        res = await self._run(
            "pair", _host_port(host, port), code, check=False, timeout=30.0, quiet_args=True
        )
        out = (res.stdout + res.stderr).strip()
        if res.returncode != 0 or "Successfully paired" not in out:
            raise AdbError(f"adb pair failed: {out.replace(code, '******') or 'no output'}")

    async def tcpip(self, serial: str, port: int = LEGACY_TCP_PORT) -> None:
        """Restart adbd on the tablet in legacy TCP mode (used for tablet-hotspot mode)."""
        res = await self._run("tcpip", str(port), serial=serial)
        if "restarting" not in res.stdout.lower():
            raise AdbError(f"adb tcpip: {(res.stdout + res.stderr).strip() or 'no output'}")

    async def tcp_port(self, serial: str) -> int:
        """The port adbd listens on over TCP (0 when TCP mode is off)."""
        value = await self.getprop(serial, "service.adb.tcp.port")
        return int(value) if value.isdigit() else 0

    async def wait_for(self, serial: str, timeout: float = 15.0) -> bool:
        """Wait until `serial` is listed as ready again (after adbd restarts)."""
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while loop.time() < end:
            if any(d.serial == serial and d.ready for d in await self.devices()):
                return True
            await asyncio.sleep(0.5)
        return False

    async def forward(self, serial: str, local: int, remote: int) -> None:
        """Laptop port `local` reaches tablet port `remote`."""
        await self._run("forward", f"tcp:{local}", f"tcp:{remote}", serial=serial)

    async def forward_remove(self, serial: str, local: int) -> None:
        await self._run("forward", "--remove", f"tcp:{local}", serial=serial, check=False)

    async def reverse(self, serial: str, remote: int, local: int) -> None:
        """Tablet port `remote` (on 127.0.0.1) reaches laptop port `local`."""
        await self._run("reverse", f"tcp:{remote}", f"tcp:{local}", serial=serial)

    async def reverse_remove(self, serial: str, remote: int) -> None:
        await self._run("reverse", "--remove", f"tcp:{remote}", serial=serial, check=False)

    async def reverse_list(self, serial: str) -> list[tuple[int, int]]:
        res = await self._run("reverse", "--list", serial=serial)
        pairs = []
        for m in re.finditer(r"tcp:(\d+)\s+tcp:(\d+)", res.stdout):
            pairs.append((int(m.group(1)), int(m.group(2))))
        return pairs

    async def shell(self, serial: str, *cmd: str, timeout: float = DEFAULT_TIMEOUT_S) -> str:
        return (await self._run("shell", *cmd, serial=serial, timeout=timeout)).stdout

    async def install(self, serial: str, apk: Path) -> None:
        await self._run("install", "-r", str(apk), serial=serial, timeout=120.0)

    async def wifi_addrs(self, serial: str) -> list[str]:
        """The tablet's IPv4 addresses on its Wi-Fi interfaces (client or hotspot)."""
        out = await self.shell(serial, "ip", "-o", "-4", "addr", "show")
        addrs = []
        for line in out.splitlines():
            iface = line.split()[1] if len(line.split()) > 1 else ""
            if iface.startswith(("wlan", "ap", "swlan")):
                addrs += parse_ipv4_addrs(line)
        return addrs

    async def getprop(self, serial: str, name: str) -> str:
        return (await self.shell(serial, "getprop", name)).strip()

    # --- Navigation and file actions used by the control bar -------------------------------

    async def keyevent(self, serial: str, key: str) -> None:
        """Press a key on the tablet, e.g. "KEYCODE_BACK"."""
        if not re.fullmatch(r"KEYCODE_[A-Z0-9_]+", key):
            raise ValueError(f"not a key code: {key}")
        await self.shell(serial, "input", "keyevent", key)

    async def foreground_package(self, serial: str) -> str | None:
        """Package of the app on screen, or None if it cannot be told."""
        out = await self.shell(serial, "dumpsys", "activity", "activities")
        return parse_foreground_package(out)

    async def force_stop(self, serial: str, package: str) -> None:
        if not _PACKAGE.fullmatch(package):
            raise ValueError(f"not a package name: {package}")
        await self.shell(serial, "am", "force-stop", package)

    async def push(self, serial: str, local: Path, remote_dir: str) -> str:
        """Copy one file into `remote_dir` and ask Android to index it. Returns the remote path."""
        remote = remote_dir.rstrip("/") + "/" + local.name
        await self._run("push", str(local), remote, serial=serial, timeout=1800.0)
        with contextlib.suppress(AdbError):
            await self.shell(
                serial,
                "am",
                "broadcast",
                "-a",
                "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
                "-d",
                "file://" + quote(remote, safe="/"),
            )
        return remote

    async def screen_state(self, serial: str) -> tuple[bool, bool]:
        """(awake, locked) for the tablet's screen."""
        power = await self.shell(serial, "dumpsys", "power")
        # Android 16 / HyperOS reports the lock screen in the activity manager's dump.
        acts = await self.shell(serial, "dumpsys", "activity", "activities")
        awake = "mWakefulness=Awake" in power
        locked = bool(re.search(r"\b(?:is|m)?KeyguardShowing=true", acts))
        return awake, locked

    async def wake_and_show_unlock(self, serial: str) -> str:
        """Turn the screen on and bring up the PIN pad. Never enters a PIN."""
        awake, locked = await self.screen_state(serial)
        if not awake:
            await self.keyevent(serial, "KEYCODE_WAKEUP")
        if locked or not awake:
            await self.shell(serial, "wm", "dismiss-keyguard")
            _, still_locked = await self.screen_state(serial)
            return "locked" if still_locked else "unlocked"
        return "unlocked"

    async def focused_window(self, serial: str) -> tuple[str, str] | None:
        """(package, window class) of the window that has input focus right now."""
        out = await self.shell(serial, "dumpsys", "window", "displays")
        return parse_focused_window(out)

    async def type_secret(self, serial: str, text: str) -> None:
        """Type text into the focused field. Never logged; letters and digits only."""
        if not re.fullmatch(r"[A-Za-z0-9]+", text):
            raise ValueError("secret must be letters and digits")
        await self._run("shell", "input", "text", text, serial=serial, quiet_args=True)

    async def write_app_file(self, serial: str, package: str, name: str, data: bytes) -> None:
        """Write `data` into an app's private files/ directory (debuggable apps, via run-as).

        The data goes through stdin, so secrets never appear on any command line or log.
        """
        if not _PACKAGE.fullmatch(package) or not re.fullmatch(r"[\w.-]+", name):
            raise ValueError("bad package or file name")
        script = f"umask 077; mkdir -p files && cat > files/{name}"
        await self._run(
            "shell", "run-as", package, "sh", "-c", f"'{script}'", serial=serial, input=data
        )

    async def tap(self, serial: str, x: int, y: int) -> None:
        await self.shell(serial, "input", "tap", str(x), str(y))

    async def ui_dump(self, serial: str) -> str:
        """Accessibility tree (XML) of the focused window.

        Android 16 no longer prints the dump to /dev/tty, so it goes through a file in
        /data/local/tmp (readable only by the shell user) that is removed right away.
        """
        tmp = "/data/local/tmp/tablink-ui.xml"
        out = await self.shell(
            serial,
            f"uiautomator dump {tmp} >/dev/null && cat {tmp}; rm -f {tmp}",
            timeout=20.0,
        )
        start = out.find("<?xml")
        end = out.rfind("</hierarchy>")
        return out[start : end + len("</hierarchy>")] if start >= 0 and end >= 0 else ""


_FOCUS = re.compile(r"mCurrentFocus=Window\{[^}]*?\bu\d+\s+([A-Za-z][\w.]*)/([\w.$]+)\}")


def parse_focused_window(dumpsys: str) -> tuple[str, str] | None:
    m = _FOCUS.search(dumpsys)
    if not m:
        return None
    pkg, cls = m.groups()
    return pkg, (pkg + cls if cls.startswith(".") else cls)


_PACKAGE = re.compile(r"[A-Za-z][\w]*(\.[A-Za-z][\w]*)+")
_TOP_ACTIVITY = re.compile(
    r"(?:topResumedActivity|mResumedActivity|ResumedActivity)\s*[:=]\s*ActivityRecord\{"
    r"[^}]*?\bu\d+\s+([A-Za-z][\w.]*)/"
)


def parse_foreground_package(dumpsys: str) -> str | None:
    m = _TOP_ACTIVITY.search(dumpsys)
    return m.group(1) if m else None
