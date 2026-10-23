"""Control channel server: one JSON object per line, request then reply.

v0 listens on 127.0.0.1 only, so it is reachable from this laptop and from the tablet through
the USB tunnel (adb reverse), never from the Wi-Fi network. TLS with pinned keys for Wi-Fi
comes with T9. Messages never carry passwords.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from tablink import __version__

log = logging.getLogger(__name__)

MAX_LINE = 4096
Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class ControlServer:
    def __init__(self, port: int, handlers: dict[str, Handler], host: str = "127.0.0.1") -> None:
        self.host = host
        self.port = port
        self.handlers = handlers
        self._server: asyncio.Server | None = None
        self._busy = asyncio.Lock()  # one tablet action at a time

    @property
    def bound_port(self) -> int:
        assert self._server is not None
        port: int = self._server.sockets[0].getsockname()[1]
        return port

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle, self.host, self.port, limit=MAX_LINE
        )
        log.info("control channel on %s:%d", self.host, self.bound_port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def dispatch(self, msg: dict[str, Any]) -> dict[str, Any]:
        kind = msg.get("type")
        if kind == "hello":
            return {"type": "hello", "app": "tablink-laptop", "version": __version__}
        handler = self.handlers.get(kind) if isinstance(kind, str) else None
        if handler is None:
            return {"type": "error", "message": f"unknown message type: {kind!r}"}
        if self._busy.locked():
            return {"type": kind, "ok": False, "message": "Busy with another request."}
        async with self._busy:
            try:
                return await handler(msg)
            except Exception as e:  # report, keep serving
                log.exception("control %s failed", kind)
                return {"type": kind, "ok": False, "message": str(e) or type(e).__name__}

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                try:
                    line = await reader.readline()
                except (ValueError, asyncio.LimitOverrunError):
                    break  # line too long
                if not line:
                    break
                try:
                    msg = json.loads(line)
                    if not isinstance(msg, dict):
                        raise ValueError
                except ValueError:
                    reply: dict[str, Any] = {"type": "error", "message": "bad message"}
                else:
                    reply = await self.dispatch(msg)
                writer.write(json.dumps(reply).encode() + b"\n")
                await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
