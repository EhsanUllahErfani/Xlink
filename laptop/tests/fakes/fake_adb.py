"""A fake `adb` that simulates one tablet. Plug it into `Adb(runner=fake)`."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from tablink.proc import RunResult

USB_SERIAL = "TAIKO0001"


@dataclass
class FakeAdb:
    usb_attached: bool = True
    authorized: bool = True
    # Address the tablet answers on for `adb connect`, e.g. "192.0.2.20:5555".
    reachable_tcp: set[str] = field(default_factory=set)
    tcpip_enabled: bool = False
    pair_code: str = "123456"
    wlan_addr: str = "192.0.2.20"
    connected_tcp: list[str] = field(default_factory=list)
    reverses: dict[str, dict[int, int]] = field(default_factory=dict)
    forwards: dict[str, dict[int, int]] = field(default_factory=dict)
    calls: list[list[str]] = field(default_factory=list)
    pushed: list[tuple[str, str]] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)
    installed: list[str] = field(default_factory=list)
    last_input: bytes | None = None
    app_files: dict[str, bytes] = field(default_factory=dict)
    # Window focus, as (package, class); a list is consumed one entry per focus query.
    focus: list[tuple[str, str]] = field(default_factory=lambda: [("com.miui.home", "L")])
    ui_xml: str | list[str] = ""
    typed: list[str] = field(default_factory=list)
    taps: list[tuple[int, int]] = field(default_factory=list)
    props: dict[str, str] = field(
        default_factory=lambda: {
            "ro.product.cpu.abilist": "arm64-v8a,armeabi-v7a,armeabi",
            "persist.security.adbinput": "1",
            "persist.security.adbinstall": "1",
        }
    )
    packages: dict[str, int] = field(default_factory=dict)
    refuse_install: bool = False
    drawn: list[str] = field(default_factory=list)
    unlock_on_draw: bool = True
    awake: bool = True
    locked: bool = False
    has_pin: bool = True  # dismissing the keyguard only shows the PIN pad
    foreground: str = "com.example.notes"
    fail_next: str | None = None  # stderr for the next call, which then exits 1
    timeout_next: bool = False

    async def __call__(
        self, argv: Sequence[str], timeout: float, input: bytes | None = None
    ) -> RunResult:
        args = list(argv[1:])
        self.calls.append(args)
        self.last_input = input
        if self.timeout_next:
            self.timeout_next = False
            raise TimeoutError
        if self.fail_next is not None:
            err, self.fail_next = self.fail_next, None
            return RunResult(1, "", err)
        serial = None
        if args[:1] == ["-s"]:
            serial, args = args[1], args[2:]
        cmd, rest = args[0], args[1:]
        handler = getattr(self, f"_{cmd}", None)
        if handler is None:
            return RunResult(1, "", f"unknown command {cmd}")
        if serial is not None and serial not in self._serials():
            return RunResult(1, "", f"adb: device '{serial}' not found")
        result: RunResult = handler(serial, rest)
        return result

    def _serials(self) -> list[str]:
        out = [USB_SERIAL] if self.usb_attached else []
        return out + self.connected_tcp

    def _devices(self, serial: str | None, rest: list[str]) -> RunResult:
        lines = ["List of devices attached"]
        if self.usb_attached:
            state = "device" if self.authorized else "unauthorized"
            lines.append(
                f"{USB_SERIAL}\t{state} usb:1-2 product:taiko_global model:24074RPD2G "
                "device:taiko transport_id:1"
            )
        for i, t in enumerate(self.connected_tcp, start=2):
            lines.append(f"{t}\tdevice product:taiko_global model:24074RPD2G transport_id:{i}")
        return RunResult(0, "\n".join(lines) + "\n\n", "")

    def _connect(self, serial: str | None, rest: list[str]) -> RunResult:
        target = rest[0]
        if target in self.connected_tcp:
            return RunResult(0, f"already connected to {target}\n", "")
        if target in self.reachable_tcp or (self.tcpip_enabled and target.endswith(":5555")):
            self.connected_tcp.append(target)
            return RunResult(0, f"connected to {target}\n", "")
        return RunResult(1, f"failed to connect to {target}: Connection refused\n", "")

    def _disconnect(self, serial: str | None, rest: list[str]) -> RunResult:
        if rest and rest[0] in self.connected_tcp:
            self.connected_tcp.remove(rest[0])
        return RunResult(0, f"disconnected {rest[0] if rest else 'everything'}\n", "")

    def _pair(self, serial: str | None, rest: list[str]) -> RunResult:
        if rest[1] == self.pair_code:
            return RunResult(0, f"Successfully paired to {rest[0]} [guid=adb-X]\n", "")
        return RunResult(1, "Failed: Wrong password or connection was dropped.\n", "")

    def _tcpip(self, serial: str | None, rest: list[str]) -> RunResult:
        self.tcpip_enabled = True
        return RunResult(0, f"restarting in TCP mode port: {rest[0]}\n", "")

    def _ports(
        self, table: dict[str, dict[int, int]], serial: str | None, rest: list[str]
    ) -> RunResult:
        ports = table.setdefault(serial or USB_SERIAL, {})
        if rest[0] == "--list":
            out = "".join(f"{serial} tcp:{a} tcp:{b}\n" for a, b in ports.items())
            return RunResult(0, out, "")
        if rest[0] == "--remove":
            ports.pop(int(rest[1].removeprefix("tcp:")), None)
            return RunResult(0, "", "")
        ports[int(rest[0].removeprefix("tcp:"))] = int(rest[1].removeprefix("tcp:"))
        return RunResult(0, "", "")

    def _reverse(self, serial: str | None, rest: list[str]) -> RunResult:
        return self._ports(self.reverses, serial, rest)

    def _forward(self, serial: str | None, rest: list[str]) -> RunResult:
        return self._ports(self.forwards, serial, rest)

    def _shell(self, serial: str | None, rest: list[str]) -> RunResult:
        if rest[:2] == ["getprop", "service.adb.tcp.port"]:
            return RunResult(0, "5555\n" if self.tcpip_enabled else "\n", "")
        if rest[:1] == ["getprop"] and rest[1] in self.props:
            return RunResult(0, self.props[rest[1]] + "\n", "")
        if rest[:4] == ["pm", "list", "packages", "--show-versioncode"]:
            pkg = rest[4]
            if pkg in self.packages:
                return RunResult(0, f"package:{pkg} versionCode:{self.packages[pkg]}\n", "")
            return RunResult(0, "", "")
        if rest[:2] == ["getprop", "ro.product.device"]:
            return RunResult(0, "taiko\n", "")
        if rest[:1] == ["ip"]:
            return RunResult(
                0,
                "1: lo    inet 127.0.0.1/8 scope host lo\n"
                f"30: wlan0    inet {self.wlan_addr}/24 brd 192.0.2.255 scope global wlan0\n"
                "31: rmnet_data0    inet 10.0.0.9/30 scope global rmnet_data0\n",
                "",
            )
        if rest[:3] == ["dumpsys", "activity", "activities"]:
            return RunResult(
                0,
                "  topResumedActivity=ActivityRecord{1 u0 " + self.foreground + "/.Main t9}\n"
                f"   KeyguardShowing={str(self.locked).lower()} AodShowing=false\n",
                "",
            )
        if rest[:2] == ["dumpsys", "power"]:
            return RunResult(0, f"  mWakefulness={'Awake' if self.awake else 'Asleep'}\n", "")
        if rest[:2] == ["input", "keyevent"] and rest[2] == "KEYCODE_WAKEUP":
            self.awake = True
            return RunResult(0, "", "")
        if rest[:2] == ["wm", "dismiss-keyguard"]:
            if not self.has_pin:
                self.locked = False
            return RunResult(0, "", "")
        if rest[:3] == ["dumpsys", "window", "displays"]:
            pkg, cls = self.focus[0] if len(self.focus) == 1 else self.focus.pop(0)
            return RunResult(
                0,
                f"  mCurrentFocus=null\n  mCurrentFocus=Window{{eaf5231 u0 {pkg}/{cls}}}\n",
                "",
            )
        if rest and rest[0].startswith("uiautomator dump /data/local/tmp/"):
            if isinstance(self.ui_xml, list):
                xml = self.ui_xml[0] if len(self.ui_xml) == 1 else self.ui_xml.pop(0)
            else:
                xml = self.ui_xml
            return RunResult(0, xml, "")
        if rest[:2] == ["input", "text"]:
            self.typed.append(rest[2])
            return RunResult(0, "", "")
        if rest[:3] == ["input", "keyevent", "KEYCODE_ENTER"] and self.typed:
            if self.unlock_on_draw:
                self.locked = False
            return RunResult(0, "", "")
        if rest[:2] == ["input", "tap"]:
            self.taps.append((int(rest[2]), int(rest[3])))
            return RunResult(0, "", "")
        if rest[:2] == ["am", "start"]:
            return RunResult(0, "Starting: Intent { ... }\n", "")
        if rest[:2] == ["am", "force-stop"]:
            self.stopped.append(rest[2])
            return RunResult(0, "", "")
        if rest and rest[0].startswith("input motionevent DOWN"):
            self.drawn.append(rest[0])
            if self.unlock_on_draw:
                self.locked = False
            return RunResult(0, "", "")
        if rest[:1] == ["run-as"]:
            name = rest[-1].rsplit("files/", 1)[-1].rstrip("'")
            self.app_files[f"{rest[1]}/{name}"] = self.last_input or b""
            return RunResult(0, "", "")
        if rest[:1] == ["echo"]:
            return RunResult(0, " ".join(rest[1:]) + "\n", "")
        return RunResult(0, "", "")

    def _push(self, serial: str | None, rest: list[str]) -> RunResult:
        self.pushed.append((rest[0], rest[1]))
        return RunResult(0, f"{rest[0]}: 1 file pushed, 0 skipped.\n", "")

    def _install(self, serial: str | None, rest: list[str]) -> RunResult:
        if self.refuse_install:
            return RunResult(
                1, "", "Failure [INSTALL_FAILED_USER_RESTRICTED: Install canceled by user]"
            )
        self.installed.append(rest[-1])
        return RunResult(0, "Performing Streamed Install\nSuccess\n", "")
