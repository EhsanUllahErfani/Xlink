"""tablinkd: the background daemon, plus the local socket the CLI and tray use to talk to it.

The local socket lives in $XDG_RUNTIME_DIR (mode 0700, user-only) and speaks one JSON object per
line. Path management and services are wired in from T4/T5 onwards.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from tablink import __version__

log = logging.getLogger(__name__)

REQUEST_TIMEOUT_S = 2.0


@dataclass
class ServiceStatus:
    name: str
    state: str  # "connected" | "syncing" | "offline" | "disabled"
    detail: str = ""


@dataclass
class DaemonStatus:
    running: bool
    mode: str | None = None  # active path mode, None when no path
    rtt_ms: float | None = None
    version: str = __version__
    services: list[ServiceStatus] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DaemonStatus:
        return cls(
            running=bool(d.get("running", True)),
            mode=d.get("mode"),
            rtt_ms=d.get("rtt_ms"),
            version=str(d.get("version", "")),
            services=[ServiceStatus(**s) for s in d.get("services", [])],
        )


def runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/tablink-{os.getuid()}"
    return Path(base) / "tablink"


def socket_path() -> Path:
    return runtime_dir() / "tablinkd.sock"


async def request(msg: dict[str, Any], path: Path | None = None) -> Any:
    """Send one request to the daemon and return its reply. Raises OSError if it is not running."""
    path = path or socket_path()
    async with asyncio.timeout(REQUEST_TIMEOUT_S):
        reader, writer = await asyncio.open_unix_connection(str(path))
        try:
            writer.write(json.dumps(msg).encode() + b"\n")
            await writer.drain()
            line = await reader.readline()
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
    if not line:
        raise ConnectionError("daemon closed the connection")
    return json.loads(line)


async def get_status(path: Path | None = None) -> DaemonStatus:
    try:
        reply = await request({"cmd": "status"}, path)
    except (OSError, TimeoutError, ValueError):
        return DaemonStatus(running=False)
    return DaemonStatus.from_dict(reply)


class Daemon:
    def __init__(self, sock: Path | None = None) -> None:
        self.sock = sock or socket_path()
        self.status = DaemonStatus(running=True)
        self._server: asyncio.Server | None = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readline()
            try:
                msg = json.loads(line)
                cmd = msg.get("cmd") if isinstance(msg, dict) else None
            except ValueError:
                cmd = None
            if cmd == "status":
                reply: dict[str, Any] = asdict(self.status)
            else:
                reply = {"error": f"unknown command: {cmd!r}"}
            writer.write(json.dumps(reply).encode() + b"\n")
            await writer.drain()
        finally:
            writer.close()

    async def start(self) -> None:
        self.sock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.sock.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(self._handle, path=str(self.sock))
        os.chmod(self.sock, 0o600)
        log.info("tablinkd %s listening on %s", __version__, self.sock)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        self.sock.unlink(missing_ok=True)

    async def run_forever(self) -> None:
        await self.start()
        try:
            await asyncio.Event().wait()
        finally:
            await self.stop()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(Daemon().run_forever())
