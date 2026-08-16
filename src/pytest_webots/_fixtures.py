from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ._core.registry import REGISTRY_KEY
from ._core.session import WebotsSession

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ._core.world import WebotsInstance


@pytest.fixture(scope="session")
def webots_world(request: pytest.FixtureRequest) -> Iterator[WebotsInstance]:
    spec = getattr(request, "param", None)
    if spec is None:
        pytest.skip("test requires a @pytest.mark.webots_world marker")
    config = request.config
    instance, fresh = config.stash[REGISTRY_KEY].get_or_create(spec)
    if fresh:
        hook = config.hook
        instance.on_started = lambda: hook.pytest_webots_world_started(instance=instance)
        instance.on_stopping = lambda: hook.pytest_webots_world_stopping(instance=instance)
        instance.on_crashed = lambda error: hook.pytest_webots_world_crashed(instance=instance, error=error)
        instance.on_before_reset = lambda: hook.pytest_webots_before_reset(instance=instance)
        instance.on_after_reset = lambda: hook.pytest_webots_after_reset(instance=instance)
        instance.boot()
    yield instance
    instance.shutdown()


@pytest.fixture
def webots(webots_world: WebotsInstance, request: pytest.FixtureRequest) -> Iterator[WebotsSession]:
    scope = webots_world.spec.scope
    if scope == "function":
        request.addfinalizer(webots_world.shutdown)
    elif scope in ("class", "module"):
        # Narrower than the parametrize scope.
        node_type = pytest.Class if scope == "class" else pytest.Module
        node = request.node.getparent(node_type)
        if node is not None:
            node.addfinalizer(webots_world.shutdown)
    webots_world.ensure_running()
    yield WebotsSession(webots_world)
    # Reset for the next test.
    if scope != "function" and webots_world.alive:
        webots_world.reset()
