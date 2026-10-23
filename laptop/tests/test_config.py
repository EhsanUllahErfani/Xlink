import stat
from pathlib import Path

import pytest

from tablink import config
from tablink.config import Config, ConfigError, VideoPreset


def test_missing_file_gives_defaults() -> None:
    cfg = config.load()
    assert cfg == Config()
    assert cfg.network.control_port == 47800
    assert cfg.video["low"] == VideoPreset(max_size=1024, bitrate="2M", fps=30)


def test_save_then_load_round_trips() -> None:
    cfg = Config()
    cfg.device.tablet_serial = "abc123"
    cfg.network.control_port = 47801
    cfg.remember_tablet_addr("192.0.2.10")
    cfg.video["high"] = VideoPreset(max_size=1920, bitrate="12M", fps=90)
    cfg.tailscale.enabled = True
    path = config.save(cfg)
    assert path == config.config_path()
    assert config.load() == cfg


def test_saved_file_is_user_only() -> None:
    path = config.save(Config())
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_save_leaves_no_temp_files() -> None:
    path = config.save(Config())
    config.save(Config())
    assert [p.name for p in path.parent.iterdir()] == ["config.toml"]


def test_spec_example_file_loads(tmp_path: Path) -> None:
    p = tmp_path / "c.toml"
    p.write_text(
        """
[device]
tablet_name = "Redmi Pad 2"
tablet_serial = ""

[network]
control_port = 47800
known_tablet_addrs = []
hotspot_ssid = "TabLink"

[video]
high     = { max_size = 2560, bitrate = "16M", fps = 60 }
balanced = { max_size = 1600, bitrate = "8M",  fps = 60 }
low      = { max_size = 1024, bitrate = "2M",  fps = 30 }

[tailscale]
enabled = false
"""
    )
    assert config.load(p) == Config()


def test_partial_file_keeps_other_defaults(tmp_path: Path) -> None:
    p = tmp_path / "c.toml"
    p.write_text('[video.low]\nfps = 15\n[network]\nhotspot_ssid = "Pad"\n')
    cfg = config.load(p)
    assert cfg.video["low"] == VideoPreset(max_size=1024, bitrate="2M", fps=15)
    assert cfg.network.hotspot_ssid == "Pad"
    assert cfg.network.control_port == 47800


@pytest.mark.parametrize(
    "text",
    [
        "[network]\ncontrol_port = 70000\n",
        "[network]\ncontrol_port = true\n",
        '[network]\ncontrol_port = "47800"\n',
        "[network]\nknown_tablet_addrs = [1, 2]\n",
        '[network]\nhotspot_ssid = ""\n',
        "[video.ultra]\nfps = 120\n",
        "[video.low]\nfps = 0\n",
        '[tailscale]\nenabled = "yes"\n',
        "device = 3\n",
        "not toml at all [",
    ],
)
def test_invalid_files_are_rejected(tmp_path: Path, text: str) -> None:
    p = tmp_path / "c.toml"
    p.write_text(text)
    with pytest.raises(ConfigError):
        config.load(p)


def test_remember_tablet_addr_dedupes_and_limits() -> None:
    cfg = Config()
    for i in range(10):
        cfg.remember_tablet_addr(f"192.0.2.{i}")
    cfg.remember_tablet_addr("192.0.2.5")
    addrs = cfg.network.known_tablet_addrs
    assert addrs[0] == "192.0.2.5"
    assert len(addrs) == 8
    assert len(set(addrs)) == len(addrs)


def test_config_has_no_secret_fields() -> None:
    keys = str(config.to_dict(Config())).lower()
    for word in ("password", "secret", "token", "key"):
        assert word not in keys
