"""TabLink's own server: Headscale on this laptop, a private network of just your devices.

No Tailscale account and nobody else's devices: the laptop and the tablet join this server
with one-time join keys and talk WireGuard-encrypted end to end (on Wi-Fi directly; far away
when the laptop is reachable). The only module that runs headscale.

Its data (node keys, join keys) lives in ~/.local/state/tablink/server, readable only by you.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
from pathlib import Path

from tablink.paths.netinfo import default_gateways, local_addr_towards
from tablink.proc import Runner, subprocess_runner

log = logging.getLogger(__name__)

SERVER_PORT = 47801  # TabLink's server (Headscale control + its built-in relay)
STUN_PORT = 3478  # helps the devices find a direct path
USER = "tablink"


class ServerError(RuntimeError):
    pass


def find_headscale() -> str | None:
    if env := os.environ.get("TABLINK_HEADSCALE"):
        return env
    if found := shutil.which("tablink-headscale"):
        return found
    here = Path(__file__).resolve()
    for parent in here.parents:
        p = parent / "server" / "headscale"
        if p.exists():
            return str(p)
    return None


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "tablink" / "server"


def socket_path() -> Path:
    # Unix socket paths must stay short (< 108 bytes).
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return Path(base) / "tablink" / "hs.sock"


def lan_address() -> str | None:
    """This laptop's address on its current network (what the tablet dials)."""
    gws = default_gateways()
    return local_addr_towards(gws[0]) if gws else None


def config_text(host: str, folder: Path, sock: Path) -> str:
    return f"""# Written by TabLink; regenerated when the laptop's network changes.
server_url: http://{host}:{SERVER_PORT}
listen_addr: 0.0.0.0:{SERVER_PORT}
metrics_listen_addr: 127.0.0.1:{SERVER_PORT + 10}
grpc_listen_addr: 127.0.0.1:{SERVER_PORT + 20}
grpc_allow_insecure: false
noise:
  private_key_path: {folder}/noise_private.key
prefixes:
  v4: 100.64.0.0/10
  v6: fd7a:115c:a1e0::/48
  allocation: sequential
derp:
  server:
    enabled: true
    region_id: 999
    region_code: tablink
    region_name: TabLink laptop
    stun_listen_addr: 0.0.0.0:{STUN_PORT}
    private_key_path: {folder}/derp_server_private.key
    automatically_add_embedded_derp_region: true
  urls: []
  paths: []
  auto_update_enabled: false
  update_frequency: 24h
disable_check_updates: true
node:
  ephemeral:
    inactivity_timeout: 30m
database:
  type: sqlite
  sqlite:
    path: {folder}/db.sqlite
log:
  level: warn
dns:
  magic_dns: false
  override_local_dns: false
  nameservers:
    global: []
unix_socket: {sock}
unix_socket_permission: "0700"
policy:
  mode: file
  path: ""
"""


class TabLinkServer:
    def __init__(
        self,
        runner: Runner | None = None,
        folder: Path | None = None,
        sock: Path | None = None,
        binary: str | None = None,
        address: object = lan_address,
    ) -> None:
        self._run = runner or subprocess_runner
        self.folder = folder or state_dir()
        self.sock = sock or socket_path()
        self.binary = binary or find_headscale()
        self.address = address  # () -> str | None
        self.host: str | None = None
        self._proc: asyncio.subprocess.Process | None = None

    @property
    def config(self) -> Path:
        return self.folder / "config.yaml"

    @property
    def url(self) -> str | None:
        return f"http://{self.host}:{SERVER_PORT}" if self.host else None

    def _write_config(self, host: str) -> None:
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.sock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.config.write_text(config_text(host, self.folder, self.sock))
        self.config.chmod(0o600)

    async def _cli(self, *args: str) -> str:
        if self.binary is None:
            raise ServerError("TabLink's server is not built (run scripts/install.sh)")
        res = await self._run([self.binary, "-c", str(self.config), *args, "-o", "json"], 30.0)
        if res.returncode != 0:
            raise ServerError(f"headscale {args[0]}: {(res.stderr or res.stdout).strip()[:200]}")
        return res.stdout

    async def ensure_running(self) -> str:
        """Start (or restart for a new network) and return the server URL."""
        if self.binary is None:
            raise ServerError("TabLink's server is not built (run scripts/install.sh)")
        host = self.address() or "127.0.0.1"  # type: ignore[operator]
        running = self._proc is not None and self._proc.returncode is None
        if running and host == self.host:
            return self.url or ""
        if self._proc is None and await self._answering() and self._config_host() == host:
            # Already run by the TabLink service (e.g. `tablink pair` in another process).
            self.host = host
            return self.url or ""
        await self.stop()
        await asyncio.to_thread(self._write_config, host)
        self._proc = await asyncio.create_subprocess_exec(
            self.binary, "serve", "-c", str(self.config),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )  # fmt: skip
        self.host = host
        for _ in range(40):  # wait for its socket
            if self.sock.exists():
                break
            await asyncio.sleep(0.25)
        log.info("TabLink server running for this network")
        return self.url or ""

    async def _answering(self) -> bool:
        if not (self.sock.exists() and self.config.exists()):
            return False
        try:
            await self._cli("users", "list")
        except ServerError:
            return False
        return True

    def _config_host(self) -> str | None:
        try:
            for line in self.config.read_text().splitlines():
                if line.startswith("server_url: http://"):
                    return line.split("//", 1)[1].rsplit(":", 1)[0]
        except OSError:
            pass
        return None

    async def user_id(self) -> int:
        users = json.loads(await self._cli("users", "list") or "[]")
        for u in users or []:
            if u.get("name") == USER:
                return int(u["id"])
        await self._cli("users", "create", USER)
        users = json.loads(await self._cli("users", "list") or "[]")
        return int(next(u["id"] for u in users if u.get("name") == USER))

    async def join_key(self, expiration: str = "1h") -> str:
        """A one-time join key for one device. Secret: never log it."""
        uid = await self.user_id()
        out = await self._cli(
            "preauthkeys", "create", "--user", str(uid), "--expiration", expiration
        )
        data = json.loads(out)
        key = data.get("key") if isinstance(data, dict) else None
        if not key:
            raise ServerError("the server did not return a join key")
        return str(key)

    async def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.returncode is None:
            proc.terminate()
            try:
                async with asyncio.timeout(5):
                    await proc.wait()
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
