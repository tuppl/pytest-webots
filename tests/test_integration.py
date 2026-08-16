import pytest

from pytest_webots import WebotsSession
from pytest_webots._core.config import discover_webots_home

pytestmark = pytest.mark.skipif(discover_webots_home(None) is None, reason="no Webots installation found")


@pytest.mark.webots_world("worlds/minimal.wbt")
def test_boot_reaches_readiness(webots: WebotsSession) -> None:
    assert webots.world.alive
    assert "probe" in webots.world.robots
    assert webots.world.robots["probe"].startswith("ipc://")


@pytest.mark.webots_world("worlds/minimal.wbt")
def test_session_scope_reuses_instance(webots: WebotsSession, world_boots: list[str]) -> None:
    assert webots.world.alive
    assert world_boots == ["minimal.wbt"]


@pytest.mark.webots_world("worlds/second.wbt", scope="function")
@pytest.mark.parametrize("run", [1, 2])
def test_function_scope_reboots_each_test(webots: WebotsSession, world_boots: list[str], run: int) -> None:
    assert webots.world.alive
    assert world_boots.count("second.wbt") == run
