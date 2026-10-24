"""F6: a shell on the laptop for the tablet's Terminal screen. The only module that starts it.

The tablet asks on an authenticated control connection ({"type": "terminal"}); from then on
that connection carries raw terminal bytes both ways: what the user types goes to the shell's
pseudo-terminal, everything the shell prints streams back live (e.g. `python3 train.py`).
Closing the connection hangs up the shell.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import logging
import os
import pty
import signal
import struct
import termios
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def _set_size(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


class Terminal:
    def __init__(self, shell: str | None = None, cwd: Path | None = None) -> None:
        self.shell = shell or os.environ.get("SHELL") or "/bin/bash"
        self.cwd = cwd or Path.home()

    async def attach(
        self, msg: dict[str, Any], reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        rows = int(msg.get("rows") or 40)
        cols = int(msg.get("cols") or 100)
        master, slave = pty.openpty()
        _set_size(master, max(5, min(rows, 300)), max(20, min(cols, 500)))
        env = {**os.environ, "TERM": "xterm-256color", "TABLINK_TERMINAL": "1"}
        proc = await asyncio.create_subprocess_exec(
            self.shell,
            "-l",
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=self.cwd,
            env=env,
            start_new_session=True,
        )
        os.close(slave)
        os.set_blocking(master, False)
        loop = asyncio.get_running_loop()
        out_q: asyncio.Queue[bytes] = asyncio.Queue()

        def readable() -> None:
            try:
                data = os.read(master, 65536)
            except OSError:
                data = b""
            out_q.put_nowait(data)
            if not data:
                loop.remove_reader(master)

        loop.add_reader(master, readable)

        async def to_tablet() -> None:
            while True:
                data = await out_q.get()
                if not data:
                    return
                writer.write(data)
                await writer.drain()

        async def to_shell() -> None:
            while True:
                data = await reader.read(65536)
                if not data:
                    return
                os.write(master, data)

        tasks = [asyncio.create_task(to_tablet()), asyncio.create_task(to_shell())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            loop.remove_reader(master)
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGHUP)
                try:
                    async with asyncio.timeout(3):
                        await proc.wait()
                except TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(proc.pid, signal.SIGKILL)
                    await proc.wait()
            os.close(master)
