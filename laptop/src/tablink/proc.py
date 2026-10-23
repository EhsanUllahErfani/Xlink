"""Run one external program. Used only by the tool wrapper modules (adb.py, services/, paths/).

Wrappers take a `Runner` so tests replace real programs with fakes from tests/fakes/.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[Sequence[str], float], Awaitable[RunResult]]


async def subprocess_runner(argv: Sequence[str], timeout: float) -> RunResult:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(timeout):
            out, err = await proc.communicate()
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        raise
    return RunResult(
        proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")
    )


class Process(Protocol):
    """A long-running child program (scrcpy window, RustDesk, ...)."""

    @property
    def returncode(self) -> int | None: ...

    async def wait(self) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def output_tail(self) -> list[str]:
        """The last lines the program printed (stdout and stderr)."""
        ...


Spawner = Callable[[Sequence[str], Mapping[str, str] | None], Awaitable[Process]]


class _ChildProcess:
    def __init__(self, proc: asyncio.subprocess.Process, tail_lines: int) -> None:
        self._proc = proc
        self._tail: deque[str] = deque(maxlen=tail_lines)
        self._reader = asyncio.ensure_future(self._read())

    async def _read(self) -> None:
        assert self._proc.stdout is not None
        async for raw in self._proc.stdout:
            self._tail.append(raw.decode(errors="replace").rstrip())

    @property
    def returncode(self) -> int | None:
        return self._proc.returncode

    async def wait(self) -> int:
        code = await self._proc.wait()
        with contextlib.suppress(Exception):
            await self._reader
        return code

    def terminate(self) -> None:
        with contextlib.suppress(ProcessLookupError):
            self._proc.terminate()

    def kill(self) -> None:
        with contextlib.suppress(ProcessLookupError):
            self._proc.kill()

    def output_tail(self) -> list[str]:
        return list(self._tail)


async def subprocess_spawner(
    argv: Sequence[str], env: Mapping[str, str] | None = None, tail_lines: int = 40
) -> Process:
    full_env = {**os.environ, **env} if env else None
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=full_env,
    )
    return _ChildProcess(proc, tail_lines)


async def stop_process(proc: Process, grace_s: float = 3.0) -> None:
    """Ask a process to exit, then kill it if it does not within `grace_s`."""
    if proc.returncode is not None:
        return
    proc.terminate()
    try:
        async with asyncio.timeout(grace_s):
            await proc.wait()
    except TimeoutError:
        proc.kill()
        await proc.wait()
