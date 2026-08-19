from pathlib import Path

import pytest

from pytest_webots import WebotsCrashedError, WebotsSession
from pytest_webots._core.config import discover_webots_home

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
    assert world_boots.count("minimal.wbt") == 1


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
    assert world_boots.count("minimal.wbt") == 1  # reload is not a reboot


@pytest.mark.webots_world(MINIMAL)
def test_crash_raises_webots_crashed_error(webots: WebotsSession) -> None:
    webots.world.kill()  # group kill: on Linux, webots is a wrapper whose child must die too
    with pytest.raises(WebotsCrashedError):
        webots.step()


@pytest.mark.webots_world(MINIMAL)
def test_crash_recovery_reboots(webots: WebotsSession, world_boots: list[str]) -> None:
    assert webots.world.alive
    assert webots.supervisor.getFromDef("BALL").getPosition() == pytest.approx(INITIAL_BALL)
    assert world_boots.count("minimal.wbt") == 2  # the crash above forced exactly one reboot


@pytest.mark.webots_world("worlds/second.wbt", scope="function")
@pytest.mark.parametrize("run", [1, 2])
def test_function_scope_reboots_each_test(webots: WebotsSession, world_boots: list[str], run: int) -> None:
    assert webots.world.alive
    assert world_boots.count("second.wbt") == run


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
        def test_cmd(webots):
            assert "--heartbeat=5000" in webots.world.command()
            assert webots.world.alive
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1)


def test_agent_plugin_end_to_end(pytester: pytest.Pytester) -> None:
    world = Path(__file__).parent / "worlds" / "minimal.wbt"
    pytester.makepyfile(
        agent_ext="""
        def register(agent):
            @agent.op("node_z")
            def node_z(agent, request):
                return agent.supervisor.getFromDef(request["name"]).getPosition()[2]

            @agent.op("get_node")
            def get_node(agent, request):
                return agent.supervisor.getFromDef(request["name"])
        """
    )
    pytester.makeini(
        """
        [pytest]
        webots_agent_plugins = agent_ext.py
        """
    )
    pytester.makepyfile(
        f"""
        import pytest

        @pytest.mark.webots_world({str(world)!r})
        def test_plugin_ops(webots):
            assert webots.agent_op("node_z", name="BALL") == pytest.approx(1.0)
            assert webots.ops.node_z(name="BALL") == pytest.approx(1.0)  # method-style equivalent
            node = webots.ops.get_node(name="BALL")  # object result becomes a proxy
            assert node.getPosition() == pytest.approx([0.0, 0.0, 1.0])
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1)


def test_broken_agent_plugin_fails_boot_with_traceback(pytester: pytest.Pytester) -> None:
    world = Path(__file__).parent / "worlds" / "second.wbt"
    pytester.makepyfile(agent_ext='raise RuntimeError("boom at import")\n')
    pytester.makeini(
        """
        [pytest]
        webots_agent_plugins = agent_ext.py
        """
    )
    pytester.makepyfile(
        f"""
        import pytest

        @pytest.mark.webots_world({str(world)!r})
        def test_never_runs(webots):
            pass
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*agent exited with code 3*"])
    result.stdout.fnmatch_lines(["*boom at import*"])
