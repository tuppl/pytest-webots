from pathlib import Path

import pytest

from pytest_webots._core import config as config_mod
from pytest_webots._core.config import discover_webots_home, webots_binary


def make_fake_home(root: Path) -> Path:
    binary = webots_binary(root)
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.touch()
    return root


def test_discovery_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = make_fake_home(tmp_path / "webots")
    monkeypatch.setenv("WEBOTS_HOME", str(home))
    assert discover_webots_home(None) == home


def test_discovery_explicit_beats_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_home = make_fake_home(tmp_path / "env")
    explicit_home = make_fake_home(tmp_path / "explicit")
    monkeypatch.setenv("WEBOTS_HOME", str(env_home))
    assert discover_webots_home(str(explicit_home)) == explicit_home


def test_discovery_explicit_invalid_errors(tmp_path: Path) -> None:
    with pytest.raises(pytest.UsageError, match="not found"):
        discover_webots_home(str(tmp_path / "nowhere"))


def test_discovery_none_found(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("WEBOTS_HOME", raising=False)
    monkeypatch.setattr(config_mod, "_DEFAULT_HOMES", (tmp_path / "absent",))
    assert discover_webots_home(None) is None
