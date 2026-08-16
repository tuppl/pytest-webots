from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ._core.build import run_build
from ._core.config import SETTINGS_KEY
from ._core.markers import collect_controller_specs
from ._core.registry import REGISTRY_KEY
from ._core.session import WebotsSession

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ._core.markers import ControllerSpec
    from ._core.world import WebotsInstance

SESSION_KEY: pytest.StashKey[WebotsSession] = pytest.StashKey()


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

    config = request.config
    settings = config.stash[SETTINGS_KEY]

    def builder(spec: ControllerSpec) -> None:
        if config.hook.pytest_webots_build_controller(spec=spec, config=config):
            return
        run_build(spec, settings)

    session = WebotsSession(webots_world, builder=builder)
    request.node.stash[SESSION_KEY] = session
    specs = collect_controller_specs(request.node, config.rootpath)
    for extra in config.hook.pytest_webots_controllers(item=request.node, instance=webots_world):
        specs.extend(extra)
    session.setup_controllers(specs)

    yield session

    # Reset while controllers are still connected: a dangling synchronous robot
    # would block the simulation and hang the reset's landing step.
    if scope != "function" and webots_world.alive:
        webots_world.reset()
    session.terminate_controllers()
