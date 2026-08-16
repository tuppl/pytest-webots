from __future__ import annotations

import pytest

from ._core import hookspecs

pytest_plugins = [
    "pytest_webots._options",
    "pytest_webots._configure",
    "pytest_webots._generate",
    "pytest_webots._collect",
    "pytest_webots._fixtures",
    "pytest_webots._report",
    "pytest_webots._session",
]


@pytest.hookimpl
def pytest_configure(config: pytest.Config) -> None:
    config.pluginmanager.add_hookspecs(hookspecs)
