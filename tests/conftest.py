from collections.abc import Callable
from pathlib import Path

import pytest

from pytest_webots import WebotsInstance
from pytest_webots._core.config import Settings

pytest_plugins = ["pytester"]

BOOTS: list[str] = []


def pytest_webots_world_started(instance: WebotsInstance) -> None:
    BOOTS.append(instance.spec.path.name)


@pytest.fixture
def world_boots() -> list[str]:
    return BOOTS


@pytest.fixture
def place_world(pytester: pytest.Pytester) -> Callable[[Path, str], str]:
    """
    Copy a world into the pytester project.
    """

    def place(source: Path, dest: str) -> str:
        target = pytester.path / dest
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text())
        return dest

    return place


@pytest.fixture
def make_settings() -> Callable[..., Settings]:
    """
    Build a Settings for tests; keyword arguments override individual fields.
    """

    def build(**overrides: object) -> Settings:
        defaults: dict[str, object] = {
            "home": None,
            "worlds_dir": None,
            "mode": "fast",
            "headless": True,
            "extra_args": (),
            "startup_timeout": 30.0,
            "max_restarts": 3,
            "port_base": 1234,
            "supervisor_name": "pytest-supervisor",
            "inject_supervisor": True,
            "build": True,
            "rebuild": False,
            "keep_alive": False,
            "worker_id": None,
            "make": "make",
        }
        defaults.update(overrides)
        return Settings(**defaults)  # type: ignore[arg-type]

    return build
