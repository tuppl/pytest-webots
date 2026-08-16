from __future__ import annotations

import pytest

from ._core.registry import REGISTRY_KEY


@pytest.hookimpl
def pytest_sessionfinish(session: pytest.Session) -> None:
    registry = session.config.stash.get(REGISTRY_KEY, None)
    if registry is not None:
        registry.sweep()
