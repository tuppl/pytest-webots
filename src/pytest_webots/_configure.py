from __future__ import annotations

import pytest

from ._core.config import SETTINGS_KEY, Settings
from ._core.registry import REGISTRY_KEY, WorldRegistry


@pytest.hookimpl
def pytest_configure(config: pytest.Config) -> None:
    settings = Settings.from_config(config)
    config.stash[SETTINGS_KEY] = settings
    config.stash[REGISTRY_KEY] = WorldRegistry(settings)
    config.addinivalue_line(
        "markers",
        "webots_world(path, *, scope='session', mode=None, args=None, timeout=None): "
        "run this test against the given Webots world. Stackable; the test is "
        "parametrized over the stacked markers, each with its own scope.",
    )
    config.addinivalue_line(
        "markers",
        "webots_controller(robot, path, *, build=None, args=None, env=None, cwd=None, autostart=True, "
        "protocol='ipc', ip_address=None): launch an extern controller for the named robot. "
        "Stackable, one marker per robot; protocol='tcp' connects via TCP, optionally to a remote ip_address.",
    )
