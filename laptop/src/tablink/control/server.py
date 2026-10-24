"""Control channel server: one JSON object per line, on 127.0.0.1 only.

The tablet reaches it through the adb reverse tunnel (USB or adb-over-Wi-Fi), so the port is
never exposed to the network. Because any app *on the tablet* could also dial the tunnel, every
connection must prove it holds the pairing token: the server sends a random challenge and the
client answers with HMAC-SHA256(token, challenge). The token itself never crosses the link.

After that the client may send requests (one reply each), subscribe to pushes from the laptop
(clipboard), or stream a file: {"type": "file", "name": ..., "size": N} then N raw bytes.
Messages never carry passwords; clipboard and notification text is never logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import secrets
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from tablink import __version__

log = logging.getLogger(__name__)

MAX_LINE = 1 << 20  # clipboard text can be long
MAX_FILE = 64 << 30
Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
# Requests that drive the tablet UI run one at a time.
EXCLUSIVE = {"remote_ready", "screen"}


class FileSink(Protocol):
    async def receive(self, name: str, size: int, reader: asyncio.StreamReader) -> str:
        """Store `size` bytes from `reader`; return the saved file name."""
        ...


def mac(token: str, nonce: str) -> str:
    return hmac.new(token.encode(), nonce.encode(), hashlib.sha256).hexdigest()


class Session:
    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self.writer = writer
        self.subscribed = False
        self._lock = asyncio.Lock()

    async def send(self, msg: dict[str, Any]) -> None:
        async with self._lock:
            self.writer.write(json.dumps(msg).encode() + b"\n")
            await self.writer.drain()


class ControlServer:
    def __init__(
        self,
        port: int,
        handlers: dict[str, Handler],
        host: str = "127.0.0.1",
        hello_extra: Callable[[], dict[str, Any]] | None = None,
        token: Callable[[], str | None] | None = None,
        files: FileSink | None = None,
        terminal: Any = None,
    ) -> None:
        self.terminal = terminal  # has async attach(msg, reader, writer)
        self.hello_extra = hello_extra
        self.host = host
        self.port = port
        self.handlers = handlers
        self.token = token or (lambda: None)
        self.files = files
        self.sessions: set[Session] = set()
        self._server: asyncio.Server | None = None
        self._busy = asyncio.Lock()

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
            for s in list(self.sessions):
                s.writer.close()
            await self._server.wait_closed()

    async def broadcast(self, msg: dict[str, Any]) -> int:
        """Push to every subscribed tablet session. Returns how many received it."""
        n = 0
        for s in list(self.sessions):
            if not s.subscribed:
                continue
            try:
                await s.send(msg)
                n += 1
            except (ConnectionError, OSError):
                self.sessions.discard(s)
        return n

    def _hello(self) -> dict[str, Any]:
        extra = self.hello_extra() if self.hello_extra else {}
        return {"type": "hello", "app": "tablink-laptop", "version": __version__, **extra}

    async def dispatch(self, msg: dict[str, Any]) -> dict[str, Any]:
        kind = msg.get("type")
        if kind == "hello":
            return self._hello()
        handler = self.handlers.get(kind) if isinstance(kind, str) else None
        if handler is None or not isinstance(kind, str):
            return {"type": "error", "message": f"unknown message type: {kind!r}"}
        if kind not in EXCLUSIVE:
            return await self._call(kind, handler, msg)
        if self._busy.locked():
            return {"type": kind, "ok": False, "message": "Busy with another request."}
        async with self._busy:
            return await self._call(kind, handler, msg)

    async def _call(self, kind: str, handler: Handler, msg: dict[str, Any]) -> dict[str, Any]:
        try:
            return await handler(msg)
        except Exception as e:  # report, keep serving
            log.exception("control %s failed", kind)
            return {"type": kind, "ok": False, "message": str(e) or type(e).__name__}

    async def _authenticate(self, session: Session, reader: asyncio.StreamReader) -> bool:
        token = self.token()
        nonce = secrets.token_hex(16)
        await session.send({"type": "challenge", "nonce": nonce})
        while True:
            line = await reader.readline()
            if not line:
                return False
            try:
                msg = json.loads(line)
            except ValueError:
                return False
            if not isinstance(msg, dict):
                return False
            if msg.get("type") == "hello":  # status before pairing is fine
                await session.send(self._hello())
                continue
            ok = (
                token is not None
                and msg.get("type") == "auth"
                and isinstance(msg.get("mac"), str)
                and hmac.compare_digest(msg["mac"], mac(token, nonce))
            )
            reply = {"type": "auth", "ok": ok}
            if token is None:
                reply["message"] = "Not paired yet: run `tablink pair` on the laptop."
            await session.send(reply)
            return ok

    async def _receive_file(
        self, msg: dict[str, Any], reader: asyncio.StreamReader
    ) -> dict[str, Any]:
        name, size = msg.get("name"), msg.get("size")
        if not isinstance(name, str) or not isinstance(size, int) or not 0 <= size <= MAX_FILE:
            raise ValueError("bad file header")
        if self.files is None:
            raise ValueError("file transfer is not available")
        saved = await self.files.receive(name, size, reader)
        return {"type": "file", "ok": True, "saved": saved}

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        session = Session(writer)
        try:
            if not await self._authenticate(session, reader):
                return
            self.sessions.add(session)
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
                    await session.send({"type": "error", "message": "bad message"})
                    continue
                if msg.get("type") == "terminal" and self.terminal is not None:
                    await session.send({"type": "terminal", "ok": True})
                    await self.terminal.attach(msg, reader, writer)
                    break  # the connection belonged to the terminal
                if msg.get("type") == "subscribe":
                    session.subscribed = True
                    await session.send({"type": "subscribe", "ok": True})
                elif msg.get("type") == "file":
                    try:
                        reply = await self._receive_file(msg, reader)
                    except (ValueError, OSError, asyncio.IncompleteReadError) as e:
                        await session.send({"type": "file", "ok": False, "message": str(e)})
                        break  # the stream position is unknown now
                    await session.send(reply)
                else:
                    await session.send(await self.dispatch(msg))
        except ConnectionError:
            pass
        finally:
            self.sessions.discard(session)
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
