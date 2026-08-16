"""
Hook specifications for pytest-webots.

Implement these in a ``conftest.py`` like any pytest hook; no import of this
module is needed.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from .markers import ControllerSpec
    from .world import WebotsInstance


@pytest.hookspec(firstresult=True)
def pytest_webots_resolve_world(name: str, config: pytest.Config) -> Path | None:
    """
    Resolve a ``webots_world`` marker name to a world file path.

    Return ``None`` to fall back to the built-in resolution chain.
    """


def pytest_webots_world_args(world: Path, config: pytest.Config) -> list[str]:
    """
    Return extra command line arguments for launching Webots with ``world``.
    """
    return []


def pytest_webots_world_started(instance: WebotsInstance) -> None:
    """
    Called after a Webots instance has booted and reached readiness.
    """


def pytest_webots_world_stopping(instance: WebotsInstance) -> None:
    """
    Called before a Webots instance is shut down.
    """


def pytest_webots_world_crashed(instance: WebotsInstance, error: BaseException) -> None:
    """
    Called when a Webots instance is detected dead or hung, before any restart.
    """


def pytest_webots_controllers(item: pytest.Item, instance: WebotsInstance) -> list[ControllerSpec]:
    """
    Return additional controller specs to launch for ``item``.
    """
    return []


@pytest.hookspec(firstresult=True)
def pytest_webots_build_controller(spec: ControllerSpec, config: pytest.Config) -> bool | None:
    """
    Build the controller for ``spec``; runs before the built-in backends.

    The integration point for build systems the plugin does not ship (cmake,
    bazel, ...): dispatch on ``spec.build``, build, and return ``True`` to mark
    it handled. Return ``None`` to fall back to the built-in backends.
    """


def pytest_webots_before_reset(instance: WebotsInstance) -> None:
    """
    Called before the between-test simulation reset.
    """


def pytest_webots_after_reset(instance: WebotsInstance) -> None:
    """
    Called after the between-test simulation reset.
    """
