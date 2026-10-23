"""F2: the tablet controls the laptop, through GNOME Remote Desktop (RDP).

Chosen over RustDesk because it is built into Ubuntu and shares the live Wayland session
without a consent prompt at every connection. The tablet connects with aFreeRDP to
127.0.0.1:3389 over USB (adb reverse) or to the laptop's address over Wi-Fi.

The only module that runs grdctl and openssl. The RDP password is created here, kept by
GNOME Remote Desktop in the GNOME keyring, and never written to files, argv or logs.
"""

from __future__ import annotations

import logging
import re
import secrets
import socket
import string
from dataclasses import dataclass
from pathlib import Path

from tablink.proc import Runner, RunResult, TtyRunner, subprocess_runner, tty_runner
from tablink.systemd import Systemd

log = logging.getLogger(__name__)

RDP_PORT = 3389
RDP_USER = "tablink"
UNIT = "gnome-remote-desktop.service"
PASSWORD_ALPHABET = string.ascii_letters + string.digits  # safe for `adb shell input text`
PASSWORD_LENGTH = 24


class RemoteDesktopError(RuntimeError):
    pass


@dataclass
class RdpStatus:
    enabled: bool = False
    unit_active: bool = False
    port: int = RDP_PORT
    view_only: bool = True
    has_tls: bool = False
    username: str = ""

    @property
    def ready(self) -> bool:
        return (
            self.enabled
            and self.unit_active
            and not self.view_only
            and self.has_tls
            and self.username == RDP_USER
        )


_NONE = {"", "(null)", "(empty)"}


def parse_status(text: str) -> RdpStatus:
    """Parse `grdctl status` (RDP section and unit status)."""
    st = RdpStatus()
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.endswith(":") and not raw.startswith("\t"):
            section = line[:-1].lower()
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "unit status":
            st.unit_active = value == "active"
        if section != "rdp":
            continue
        if key == "status":
            st.enabled = value == "enabled"
        elif key == "port" and value.isdigit():
            st.port = int(value)
        elif key == "view-only":
            st.view_only = value == "yes"
        elif key == "tls fingerprint":
            st.has_tls = value not in _NONE
        elif key == "username":
            st.username = "" if value in _NONE else value
    return st


def parse_password(text: str) -> str | None:
    m = re.search(r"^\s*Password:\s*(\S+)\s*$", text, re.MULTILINE)
    if not m or m.group(1) in _NONE:
        return None
    return m.group(1)


def new_password() -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(PASSWORD_LENGTH))


def tls_dir() -> Path:
    return Path.home() / ".local" / "share" / "gnome-remote-desktop"


class RemoteDesktop:
    def __init__(
        self,
        runner: Runner | None = None,
        tty: TtyRunner | None = None,
        systemd: Systemd | None = None,
        cert_dir: Path | None = None,
    ) -> None:
        self._run = runner or subprocess_runner
        self._tty = tty or tty_runner
        self.systemd = systemd or Systemd(self._run)
        self.cert_dir = cert_dir or tls_dir()

    async def _grdctl(self, *args: str, check: bool = True) -> RunResult:
        res = await self._run(["grdctl", *args], 15.0)
        if check and res.returncode != 0:
            msg = (res.stderr or res.stdout).strip().splitlines()
            raise RemoteDesktopError(f"grdctl {' '.join(args[:2])}: {msg[-1] if msg else 'failed'}")
        return res

    async def status(self) -> RdpStatus:
        # --show-credentials is needed to see the username; the password line is ignored.
        res = await self._grdctl("status", "--show-credentials", check=False)
        return parse_status(res.stdout)

    async def password(self) -> str | None:
        """The RDP password, for typing into the tablet. Never log the result."""
        res = await self._grdctl("status", "--show-credentials", check=False)
        return parse_password(res.stdout)

    async def _ensure_tls(self) -> None:
        cert, key = self.cert_dir / "tls.crt", self.cert_dir / "tls.key"
        if not (cert.exists() and key.exists()):
            self.cert_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            argv = [
                "openssl", "req", "-x509", "-newkey", "rsa:3072", "-sha256", "-days", "3650",
                "-nodes", "-subj", f"/CN={socket.gethostname()} TabLink",
                "-keyout", str(key), "-out", str(cert),
            ]  # fmt: skip
            res = await self._run(argv, 60.0)
            if res.returncode != 0:
                raise RemoteDesktopError(
                    f"could not create a TLS certificate: {res.stderr.strip()}"
                )
            key.chmod(0o600)
        await self._grdctl("rdp", "set-tls-cert", str(cert))
        await self._grdctl("rdp", "set-tls-key", str(key))

    async def setup(self, rotate_password: bool = False) -> RdpStatus:
        """Make the laptop reachable over RDP with input enabled. Safe to run again."""
        st = await self.status()
        if not st.has_tls:
            await self._ensure_tls()
        if rotate_password or st.username != RDP_USER or await self.password() is None:
            res = await self._tty(
                ["grdctl", "rdp", "set-credentials"], [RDP_USER, new_password()], 15.0
            )
            if res.returncode != 0:
                raise RemoteDesktopError("could not set the remote desktop password")
        await self._grdctl("rdp", "set-port", str(RDP_PORT))
        await self._grdctl("rdp", "disable-port-negotiation")
        await self._grdctl("rdp", "disable-view-only")
        await self._grdctl("rdp", "enable")
        if not await self.systemd.is_active(UNIT):
            await self.systemd.enable_now(UNIT)
        st = await self.status()
        if not st.ready:
            raise RemoteDesktopError(f"remote desktop is not ready: {st}")
        log.info("remote desktop ready on port %d", st.port)
        return st
