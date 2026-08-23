from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

import pytest

from ._core.build import run_build
from ._core.config import SETTINGS_KEY
from ._core.markers import collect_controller_specs
from ._core.registry import REGISTRY_KEY
from ._core.session import WebotsSession

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ._core.config import Settings
    from ._core.markers import ControllerSpec
    from ._core.world import WebotsInstance

SESSION_KEY: pytest.StashKey[WebotsSession] = pytest.StashKey()

# Scopes narrower than the parametrize scope pytest finalizes on; session has
# no entry because pytest already owns that boundary.
_NARROWER_SCOPES = {"function": pytest.Function, "class": pytest.Class, "module": pytest.Module}


def _register_world_finalizer(request: pytest.FixtureRequest, instance: WebotsInstance) -> None:
    """
    Shut a world down at its own boundary when its scope is the narrower one.
    """
    node_type = _NARROWER_SCOPES.get(instance.spec.scope)
    node = request.node.getparent(node_type) if node_type else None
    if node is not None:
        node.addfinalizer(instance.shutdown)


def _build(spec: ControllerSpec, config: pytest.Config, settings: Settings) -> None:
    if config.hook.pytest_webots_build_controller(spec=spec, config=config):
        return
    run_build(spec, settings)


def _leave_world_clean(session: WebotsSession, instance: WebotsInstance) -> None:
    """
    Leave the world fit for the next test, whether this one finished or failed
    in setup: re-crew any robot whose controller departed so the reset's landing
    step is not blocked, reset, then drop this test's controllers.

    A function-scoped world is about to be shut down, so there is nothing to
    preserve.
    """
    try:
        if instance.spec.scope != "function" and instance.alive:
            session.recrew_departed()
            if instance.alive:
                instance.reset()
    finally:
        session.terminate_controllers()


@pytest.fixture(scope="session")
def _webots_world(request: pytest.FixtureRequest) -> Iterator[WebotsInstance]:
    spec = getattr(request, "param", None)
    if spec is None:
        pytest.skip("test requires a @pytest.mark.webots_world marker")
    config = request.config
    instance, fresh = config.stash[REGISTRY_KEY].get_or_create(spec)
    if fresh:
        hook = config.hook
        instance.hook_args = tuple(
            arg for args in hook.pytest_webots_world_args(world=spec.path, config=config) for arg in args
        )
        instance.on_started = lambda: hook.pytest_webots_world_started(instance=instance)
        instance.on_stopping = lambda: hook.pytest_webots_world_stopping(instance=instance)
        instance.on_crashed = lambda error: hook.pytest_webots_world_crashed(instance=instance, error=error)
        instance.on_before_reset = lambda: hook.pytest_webots_before_reset(instance=instance)
        instance.on_after_reset = lambda: hook.pytest_webots_after_reset(instance=instance)
    instance.ensure_running()
    yield instance
    instance.shutdown()


@pytest.fixture
def webots(_webots_world: WebotsInstance, request: pytest.FixtureRequest) -> Iterator[WebotsSession]:
    _register_world_finalizer(request, _webots_world)
    _webots_world.ensure_running()

    config = request.config
    session = WebotsSession(
        _webots_world,
        builder=partial(_build, config=config, settings=config.stash[SETTINGS_KEY]),
    )
    request.node.stash[SESSION_KEY] = session
    specs = collect_controller_specs(request.node, config.rootpath, request.getfixturevalue)
    for extra in config.hook.pytest_webots_controllers(item=request.node, instance=_webots_world):
        specs.extend(extra)
    try:
        session.setup_controllers(specs)
    except BaseException:
        _leave_world_clean(session, _webots_world)
        raise

    yield session

    _leave_world_clean(session, _webots_world)
