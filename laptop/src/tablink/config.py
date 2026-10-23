"""TabLink configuration: ~/.config/tablink/config.toml.

Secrets (hotspot password, pairing keys) never live here; they go in GNOME Keyring.
"""

from __future__ import annotations

import os
import tempfile
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import tomli_w

PRESET_NAMES = ("high", "balanced", "low")


class ConfigError(ValueError):
    """The config file exists but is not valid."""


@dataclass
class DeviceConfig:
    tablet_name: str = "Redmi Pad 2"
    tablet_serial: str = ""


@dataclass
class NetworkConfig:
    control_port: int = 47800
    known_tablet_addrs: list[str] = field(default_factory=list)
    hotspot_ssid: str = "TabLink"


@dataclass
class VideoPreset:
    max_size: int
    bitrate: str
    fps: int


def _default_presets() -> dict[str, VideoPreset]:
    return {
        "high": VideoPreset(max_size=2560, bitrate="16M", fps=60),
        "balanced": VideoPreset(max_size=1600, bitrate="8M", fps=60),
        "low": VideoPreset(max_size=1024, bitrate="2M", fps=30),
    }


@dataclass
class TailscaleConfig:
    enabled: bool = False


@dataclass
class RemoteConfig:
    # aFreeRDP saved-connection id holding the laptop login; 0 = not created yet.
    afreerdp_bookmark: int = 0


@dataclass
class Config:
    device: DeviceConfig = field(default_factory=DeviceConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    video: dict[str, VideoPreset] = field(default_factory=_default_presets)
    tailscale: TailscaleConfig = field(default_factory=TailscaleConfig)
    remote: RemoteConfig = field(default_factory=RemoteConfig)

    def remember_tablet_addr(self, addr: str, limit: int = 8) -> None:
        """Put `addr` first in the known address list, keeping the list short."""
        addrs = [a for a in self.network.known_tablet_addrs if a != addr]
        self.network.known_tablet_addrs = [addr, *addrs][:limit]


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "tablink"


def config_path() -> Path:
    return config_dir() / "config.toml"


def _expect(value: Any, kind: type | tuple[type, ...], where: str) -> Any:
    # bool is an int subclass; never accept it where a number is expected.
    if isinstance(value, bool) and kind is not bool:
        raise ConfigError(f"{where}: expected {kind}, got bool")
    if not isinstance(value, kind):
        raise ConfigError(f"{where}: expected {kind}, got {type(value).__name__}")
    return value


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    return dict(_expect(value, dict, f"[{name}]"))


def from_dict(raw: dict[str, Any]) -> Config:
    """Build a Config from parsed TOML, filling defaults for anything missing."""
    cfg = Config()

    dev = _section(raw, "device")
    if "tablet_name" in dev:
        cfg.device.tablet_name = _expect(dev["tablet_name"], str, "device.tablet_name")
    if "tablet_serial" in dev:
        cfg.device.tablet_serial = _expect(dev["tablet_serial"], str, "device.tablet_serial")

    net = _section(raw, "network")
    if "control_port" in net:
        port = _expect(net["control_port"], int, "network.control_port")
        if not 1 <= port <= 65535:
            raise ConfigError(f"network.control_port: {port} is not a valid port")
        cfg.network.control_port = port
    if "known_tablet_addrs" in net:
        addrs = _expect(net["known_tablet_addrs"], list, "network.known_tablet_addrs")
        for a in addrs:
            _expect(a, str, "network.known_tablet_addrs[]")
        cfg.network.known_tablet_addrs = list(addrs)
    if "hotspot_ssid" in net:
        ssid = _expect(net["hotspot_ssid"], str, "network.hotspot_ssid")
        if not 1 <= len(ssid.encode()) <= 32:
            raise ConfigError("network.hotspot_ssid: must be 1 to 32 bytes")
        cfg.network.hotspot_ssid = ssid

    video = _section(raw, "video")
    for name, preset in video.items():
        if name not in PRESET_NAMES:
            raise ConfigError(f"video.{name}: unknown preset (use {', '.join(PRESET_NAMES)})")
        p = dict(_expect(preset, dict, f"video.{name}"))
        base = cfg.video[name]
        max_size = _expect(p.get("max_size", base.max_size), int, f"video.{name}.max_size")
        bitrate = _expect(p.get("bitrate", base.bitrate), str, f"video.{name}.bitrate")
        fps = _expect(p.get("fps", base.fps), int, f"video.{name}.fps")
        if max_size <= 0 or fps <= 0:
            raise ConfigError(f"video.{name}: max_size and fps must be positive")
        cfg.video[name] = VideoPreset(max_size=max_size, bitrate=bitrate, fps=fps)

    ts = _section(raw, "tailscale")
    if "enabled" in ts:
        cfg.tailscale.enabled = _expect(ts["enabled"], bool, "tailscale.enabled")

    rem = _section(raw, "remote")
    if "afreerdp_bookmark" in rem:
        bm = _expect(rem["afreerdp_bookmark"], int, "remote.afreerdp_bookmark")
        if bm < 0:
            raise ConfigError("remote.afreerdp_bookmark: must be 0 or more")
        cfg.remote.afreerdp_bookmark = bm

    return cfg


def to_dict(cfg: Config) -> dict[str, Any]:
    return asdict(cfg)


def load(path: Path | None = None) -> Config:
    """Load the config, or return defaults if the file does not exist yet."""
    path = path or config_path()
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return Config()
    try:
        raw = tomllib.loads(data.decode())
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise ConfigError(f"{path}: {e}") from e
    return from_dict(raw)


def save(cfg: Config, path: Path | None = None) -> Path:
    """Write the config atomically, readable only by the user."""
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = tomli_w.dumps(to_dict(cfg)).encode()
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".config.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path
