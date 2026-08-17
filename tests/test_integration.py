from pathlib import Path

import pytest

from pytest_webots import WebotsCrashedError, WebotsSession, WorldBootTimeout, WorldSpec
from pytest_webots._core.config import Settings, discover_webots_home
from pytest_webots._core.world import WebotsInstance

pytestmark = pytest.mark.skipif(discover_webots_home(None) is None, reason="no Webots installation found")

MINIMAL = "worlds/minimal.wbt"
INITIAL_BALL = [0.0, 0.0, 1.0]


@pytest.mark.webots_world(MINIMAL)
def test_boot_reaches_readiness(webots: WebotsSession) -> None:
    assert webots.world.alive
    assert "probe" in webots.world.robots
    assert webots.world.robots["probe"].startswith("ipc://")
    assert "pytest-supervisor" in webots.world.robots


@pytest.mark.webots_world(MINIMAL)
def test_session_scope_reuses_instance(webots: WebotsSession, world_boots: list[str]) -> None:
    assert webots.world.alive
    assert world_boots == ["minimal.wbt"]


@pytest.mark.webots_world(MINIMAL)
def test_supervisor_round_trip(webots: WebotsSession) -> None:
    node = webots.supervisor.getFromDef("BALL")
    assert node.getPosition() == pytest.approx(INITIAL_BALL)
    assert webots.step(64) >= 0
    assert webots.sim_time() > 0


@pytest.mark.webots_world(MINIMAL)
def test_move_node(webots: WebotsSession) -> None:
    field = webots.supervisor.getFromDef("BALL").getField("translation")
    field.setSFVec3f([0.5, -0.5, 2.0])
    webots.step()
    assert field.getSFVec3f() == pytest.approx([0.5, -0.5, 2.0])


@pytest.mark.webots_world(MINIMAL)
def test_reset_between_tests_restored_node(webots: WebotsSession) -> None:
    field = webots.supervisor.getFromDef("BALL").getField("translation")
    assert field.getSFVec3f() == pytest.approx(INITIAL_BALL)


@pytest.mark.webots_world(MINIMAL)
def test_reload_mid_test(webots: WebotsSession, world_boots: list[str]) -> None:
    webots.reload()
    node = webots.supervisor.getFromDef("BALL")
    assert node.getPosition() == pytest.approx(INITIAL_BALL)
    assert world_boots == ["minimal.wbt"]  # reload is not a reboot


@pytest.mark.webots_world(MINIMAL)
def test_crash_raises_webots_crashed_error(webots: WebotsSession) -> None:
    webots.world.kill()  # group kill: on Linux, webots is a wrapper whose child must die too
    with pytest.raises(WebotsCrashedError):
        webots.step()


@pytest.mark.webots_world(MINIMAL)
def test_crash_recovery_reboots(webots: WebotsSession, world_boots: list[str]) -> None:
    assert webots.world.alive
    assert webots.supervisor.getFromDef("BALL").getPosition() == pytest.approx(INITIAL_BALL)
    assert world_boots == ["minimal.wbt", "minimal.wbt"]


@pytest.mark.webots_world("worlds/second.wbt", scope="function")
@pytest.mark.parametrize("run", [1, 2])
def test_function_scope_reboots_each_test(webots: WebotsSession, world_boots: list[str], run: int) -> None:
    assert webots.world.alive
    assert world_boots.count("second.wbt") == run


@pytest.mark.webots_world("worlds/second.wbt", scope="function")
@pytest.mark.parametrize("run", [1, 2])
def test_bare_webots_world_revives_after_scope_teardown(webots_world, run: int) -> None:
    assert webots_world.alive


def test_world_args_hook_extends_command(pytester: pytest.Pytester) -> None:
    world = Path(__file__).parent / "worlds" / "second.wbt"
    pytester.makeconftest(
        """
        def pytest_webots_world_args(world, config):
            return ["--heartbeat=5000"]
        """
    )
    pytester.makepyfile(
        f"""
        import pytest

        @pytest.mark.webots_world({str(world)!r})
        def test_cmd(webots_world):
            assert "--heartbeat=5000" in webots_world.command()
            assert webots_world.alive
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1)


def test_boot_timeout(tmp_path: Path) -> None:
    settings = Settings(
        home=discover_webots_home(None),
        worlds_dir=None,
        mode="fast",
        headless=True,
        extra_args=(),
        startup_timeout=5,
        max_restarts=3,
        port_base=1334,
        supervisor_name="pytest-supervisor",
        inject_supervisor=False,  # nothing will ever announce a URL
        build=True,
        rebuild=False,
        keep_alive=False,
        worker_id=None,
    )
    # Boot from a copy so the Webots GUI-state sidecar lands in tmp_path, not the repo.
    world = tmp_path / "empty.wbt"
    world.write_text((Path(__file__).parent / "worlds" / "empty.wbt").read_text())
    instance = WebotsInstance(WorldSpec(path=world, timeout=5), settings, port=1334)
    try:
        with pytest.raises(WorldBootTimeout, match="did not become ready"):
            instance.boot()
    finally:
        instance.shutdown(force=True)
    assert not instance.alive
