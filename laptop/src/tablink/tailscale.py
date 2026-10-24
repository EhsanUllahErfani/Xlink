"""TabLink's built-in Tailscale (mode 7). The only module that runs the tailnet helper.

No separate Tailscale install: `tablink-tailnet` (laptop/tailnet, Tailscale's tsnet library)
is a userspace Tailscale node run by the daemon. It speaks JSON lines on stdin/stdout. Its
state (machine and node keys) is handed to us and kept in the GNOME keyring; the helper never
writes it to disk. Used for mode 7 and as a relay for isolated Wi-Fi, never for local modes.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import shutil
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from tablink.secrets import Store

log = logging.getLogger(__name__)

REQUEST_TIMEOUT_S = 15.0


@dataclass(frozen=True)
class Peer:
    hostname: str
    os: str
    ips: tuple[str, ...]
    online: bool


@dataclass(frozen=True)
class TailnetStatus:
    running: bool
    self_ips: tuple[str, ...]
    peers: tuple[Peer, ...]
    state: str = ""
    auth_url: str = ""


def parse_status(msg: dict[str, Any]) -> TailnetStatus:
    peers = tuple(
        Peer(
            hostname=str(p.get("host", "")),
            os=str(p.get("os", "")),
            ips=tuple(p.get("ips") or ()),
            online=bool(p.get("online")),
        )
        for p in (msg.get("peers") or [])
    )
    state = str(msg.get("state", ""))
    return TailnetStatus(
        running=state == "Running",
        self_ips=tuple(msg.get("ips") or ()),
        peers=peers,
        state=state,
        auth_url=str(msg.get("auth_url") or ""),
    )


def ipv4(ips: tuple[str, ...]) -> str | None:
    return next((ip for ip in ips if ":" not in ip), ips[0] if ips else None)


def find_tablet(status: TailnetStatus, name_hint: str = "") -> Peer | None:
    """The tablet: TabLink's own node on it ("tablink-..."), else a name match, else the only
    online Android peer."""
    online = [p for p in status.peers if p.online and p.ips]
    ours = [p for p in online if p.hostname.startswith("tablink-") and p.os.lower() == "android"]
    if len(ours) == 1:
        return ours[0]
    if name_hint:
        hint = name_hint.lower().replace(" ", "-")
        named = [p for p in online if hint in p.hostname.lower()]
        if named:
            return named[0]
    android = [p for p in online if p.os.lower() == "android"]
    return android[0] if len(android) == 1 else None


def find_helper() -> str | None:
    if env := os.environ.get("TABLINK_TAILNET"):
        return env
    if found := shutil.which("tablink-tailnet"):
        return found
    here = Path(__file__).resolve()
    for parent in here.parents:
        p = parent / "tailnet" / "tablink-tailnet"
        if p.exists():
            return str(p)
    return None


class Channel(Protocol):
    async def write_line(self, line: str) -> None: ...

    async def read_line(self) -> str | None: ...

    async def close(self) -> None: ...


class _ProcessChannel:
    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self.proc = proc

    async def write_line(self, line: str) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(line.encode() + b"\n")
        await self.proc.stdin.drain()

    async def read_line(self) -> str | None:
        assert self.proc.stdout is not None
        raw = await self.proc.stdout.readline()
        return raw.decode() if raw else None

    async def close(self) -> None:
        if self.proc.returncode is None:
            self.proc.terminate()
            try:
                async with asyncio.timeout(3):
                    await self.proc.wait()
            except TimeoutError:
                self.proc.kill()
                await self.proc.wait()


async def launch_helper() -> Channel:
    helper = find_helper()
    if helper is None:
        raise FileNotFoundError("tablink-tailnet helper not found (build laptop/tailnet)")
    proc = await asyncio.create_subprocess_exec(
        helper,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,  # tsnet chatter may include URLs and keys
        limit=16 << 20,
    )
    return _ProcessChannel(proc)


def work_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "tablink" / "tailnet"


class Tailscale:
    """The built-in tailnet node. start() is lazy: the first status() starts it."""

    def __init__(
        self,
        store: Store,
        launch: Callable[[], Awaitable[Channel]] = launch_helper,
        hostname: str | None = None,
    ) -> None:
        self.store = store
        self.launch = launch
        self.hostname = hostname or "tablink-" + socket.gethostname().split(".")[0].lower()[:40]
        self._chan: Channel | None = None
        self._reader: asyncio.Task[None] | None = None
        self._replies: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._lock = asyncio.Lock()
        self._state: dict[str, str] = {}

    def _load_state(self) -> dict[str, str]:
        raw = self.store.get()
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            return {}
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}

    async def start(self) -> None:
        if self._chan is not None:
            return
        self._state = await asyncio.to_thread(self._load_state)
        chan = await self.launch()
        work = work_dir()
        work.mkdir(parents=True, exist_ok=True, mode=0o700)
        init = {"cmd": "init", "state": self._state, "dir": str(work), "hostname": self.hostname}
        await chan.write_line(json.dumps(init))
        self._chan = chan
        self._reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        assert self._chan is not None
        while True:
            line = await self._chan.read_line()
            if line is None:
                break
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("type") == "state":
                key, data = str(msg.get("key", "")), str(msg.get("data", ""))
                try:
                    base64.b64decode(data, validate=True)
                except ValueError:
                    continue
                self._state[key] = data
                snapshot = json.dumps(self._state)
                await asyncio.to_thread(self.store.set, snapshot)
            else:
                await self._replies.put(msg)
        self._chan = None

    async def _request(self, msg: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            await self.start()
            assert self._chan is not None
            while not self._replies.empty():
                self._replies.get_nowait()
            await self._chan.write_line(json.dumps(msg))
            async with asyncio.timeout(REQUEST_TIMEOUT_S):
                return await self._replies.get()

    async def status(self) -> TailnetStatus:
        try:
            reply = await self._request({"cmd": "status"})
        except (OSError, TimeoutError):
            return TailnetStatus(False, (), ())
        return parse_status(reply)

    async def login(self) -> str:
        """Start sign-in; returns the link to open ("" if already signed in)."""
        await self._request({"cmd": "login"})
        for _ in range(20):
            st = await self.status()
            if st.auth_url or st.running:
                return st.auth_url
            await asyncio.sleep(0.5)
        return ""

    async def logout(self) -> None:
        await self._request({"cmd": "logout"})
        await asyncio.to_thread(self.store.set, "{}")

    async def forward(self, listen: str, to: str) -> bool:
        reply = await self._request({"cmd": "forward", "listen": listen, "to": to})
        return bool(reply.get("ok"))

    async def stop(self) -> None:
        if self._reader:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
        if self._chan:
            await self._chan.close()
            self._chan = None
