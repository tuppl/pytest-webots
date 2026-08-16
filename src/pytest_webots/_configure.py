from __future__ import annotations

import pytest


@pytest.hookimpl
def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "webots_world(path, *, scope='session', mode=None, args=None, timeout=None): "
        "run this test against the given Webots world. Stackable; the test is "
        "parametrized over the stacked markers, each with its own scope.",
    )
    config.addinivalue_line(
        "markers",
        "webots_controller(robot, path, *, build=None, args=None, env=None, cwd=None, autostart=True): "
        "launch an extern controller for the named robot. Stackable, one marker per robot.",
    )
