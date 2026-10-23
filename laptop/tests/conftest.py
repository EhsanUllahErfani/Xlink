from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every test away from the real ~/.config and $XDG_RUNTIME_DIR."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    # Unix socket paths are limited to ~107 bytes, so use a short dir.
    run = Path("/tmp") / f"tl-{tmp_path.name[-12:]}"
    run.mkdir(exist_ok=True)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(run))
    monkeypatch.setenv("NO_COLOR", "1")
