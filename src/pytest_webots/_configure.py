from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ._core.config import SETTINGS_KEY, Settings
from ._core.registry import REGISTRY_KEY, WorldRegistry

if TYPE_CHECKING:
    from pathlib import Path

    from ._core.world import WebotsInstance


class _PytestWorldHooks:
    """
    Relays a world's notifications to this plugin's pytest hooks.

    Lives here rather than in `_core` so the domain layer never imports pytest.
    """

    def __init__(self, config: pytest.Config) -> None:
        self._hook = config.hook
        self._config = config

    def world_args(self, world: Path) -> tuple[str, ...]:
        contributed = self._hook.pytest_webots_world_args(world=world, config=self._config)
        return tuple(arg for args in contributed for arg in args)

    def started(self, instance: WebotsInstance) -> None:
        self._hook.pytest_webots_world_started(instance=instance)

    def stopping(self, instance: WebotsInstance) -> None:
        self._hook.pytest_webots_world_stopping(instance=instance)

    def crashed(self, instance: WebotsInstance, error: BaseException) -> None:
        self._hook.pytest_webots_world_crashed(instance=instance, error=error)

    def controller_departed(self, instance: WebotsInstance, robot: str) -> None:
        self._hook.pytest_webots_controller_departed(instance=instance, robot=robot)

    def before_reset(self, instance: WebotsInstance) -> None:
        self._hook.pytest_webots_before_reset(instance=instance)

    def after_reset(self, instance: WebotsInstance) -> None:
        self._hook.pytest_webots_after_reset(instance=instance)


@pytest.hookimpl
def pytest_configure(config: pytest.Config) -> None:
    settings = Settings.from_config(config)
    config.stash[SETTINGS_KEY] = settings
    config.stash[REGISTRY_KEY] = WorldRegistry(settings, hooks=_PytestWorldHooks(config))
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
