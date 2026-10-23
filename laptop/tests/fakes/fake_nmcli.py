"""A fake nmcli: one Wi-Fi device, connection add/modify/up/down for the TabLink hotspot."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from tablink.proc import RunResult


@dataclass
class FakeNmcli:
    device: str = "wlo1"
    connection: str = "SIT-Secure"
    connections: set[str] = field(default_factory=lambda: {"SIT-Secure"})
    calls: list[list[str]] = field(default_factory=list)
    psk_seen: str | None = None  # read from the passwd-file during `up`
    passwd_file: str | None = None
    fail_up: bool = False

    async def __call__(
        self, argv: Sequence[str], timeout: float, input: bytes | None = None
    ) -> RunResult:
        a = list(argv[1:])
        self.calls.append(a)
        if a[:4] == ["-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"]:
            state = "connected" if self.connection else "disconnected"
            return RunResult(
                0,
                f"{self.device}:wifi:{state}:{self.connection}\n"
                "lo:loopback:connected (externally):lo\n",
                "",
            )
        if a[:5] == ["-t", "-f", "NAME", "connection", "show"]:
            return RunResult(0, "\n".join(sorted(self.connections)) + "\n", "")
        if a[:2] == ["connection", "add"]:
            self.connections.add(a[a.index("con-name") + 1])
            return RunResult(0, "Connection added\n", "")
        if a[:2] == ["connection", "modify"]:
            return RunResult(0, "", "")
        if a[:2] == ["connection", "up"]:
            if self.fail_up:
                return RunResult(1, "", "Error: Connection activation failed: No suitable device")
            self.passwd_file = a[a.index("passwd-file") + 1]
            line = Path(self.passwd_file).read_text().strip()
            self.psk_seen = line.split(":", 1)[1]
            self.connection = a[2]
            return RunResult(0, "Connection successfully activated\n", "")
        if a[:2] == ["connection", "down"]:
            self.connection = "SIT-Secure"
            return RunResult(0, "", "")
        return RunResult(1, "", f"unexpected {a}")
