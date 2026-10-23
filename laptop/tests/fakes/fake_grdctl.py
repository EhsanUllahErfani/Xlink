"""Fake grdctl / systemctl / openssl for the remote desktop module."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from tablink.proc import RunResult


@dataclass
class FakeGrd:
    enabled: bool = False
    view_only: bool = True
    tls: bool = False
    username: str = ""
    password: str = ""
    port: int = 3389
    unit_active: bool = False
    calls: list[list[str]] = field(default_factory=list)
    tty_calls: list[tuple[list[str], list[str]]] = field(default_factory=list)

    def _status(self, creds: bool) -> str:
        user = (self.username or "(empty)") if creds else "(hidden)"
        pw = (self.password or "(empty)") if creds else "(hidden)"
        return (
            "Overall:\n"
            f"\tUnit status: {'active' if self.unit_active else 'inactive'}\n"
            "RDP:\n"
            f"\tStatus: {'enabled' if self.enabled else 'disabled'}\n"
            f"\tPort: {self.port}\n"
            f"\tTLS fingerprint: {'aa:bb' if self.tls else ''}\n"
            f"\tView-only: {'yes' if self.view_only else 'no'}\n"
            f"\tUsername: {user}\n"
            f"\tPassword: {pw}\n"
        )

    async def __call__(
        self, argv: Sequence[str], timeout: float, input: bytes | None = None
    ) -> RunResult:
        a = list(argv)
        self.calls.append(a)
        if a[0] == "openssl":
            Path(a[a.index("-keyout") + 1]).write_text("key")
            Path(a[a.index("-out") + 1]).write_text("cert")
            return RunResult(0, "", "")
        if a[0] == "systemctl":
            if a[2] == "is-active":
                return RunResult(0, "active\n" if self.unit_active else "inactive\n", "")
            self.unit_active = True
            return RunResult(0, "", "")
        sub = a[1:]
        if sub[0] == "status":
            return RunResult(0, self._status("--show-credentials" in sub), "")
        cmd = sub[1]
        if cmd in ("set-tls-cert", "set-tls-key"):
            self.tls = True
        elif cmd == "set-port":
            self.port = int(sub[2])
        elif cmd == "disable-view-only":
            self.view_only = False
        elif cmd == "enable":
            self.enabled = True
        return RunResult(0, "", "")

    async def tty(self, argv: Sequence[str], lines: Sequence[str], timeout: float) -> RunResult:
        self.tty_calls.append((list(argv), list(lines)))
        self.username, self.password = lines[0], lines[1]
        return RunResult(0, "Username: \nPassword: \n", "")
