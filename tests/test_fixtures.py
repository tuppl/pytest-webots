import time
from collections.abc import Callable
from pathlib import Path

import pytest

from pytest_webots import WebotsCrashedError, WebotsError, WebotsQuitError, WebotsSession
from pytest_webots._core.config import discover_webots_home

WaitForLog = Callable[..., None]

pytestmark = pytest.mark.skipif(discover_webots_home(None) is None, reason="no Webots installation found")

MINIMAL = "worlds/minimal.wbt"
SYNC = "worlds/sync.wbt"
PACER = Path(__file__).parent / "controllers" / "pacer" / "pacer.py"
QUITTER = Path(__file__).parent / "controllers" / "quitter" / "quitter.py"
PROBE = Path(__file__).parent / "controllers" / "probe" / "probe.py"
INITIAL_BALL = [0.0, 0.0, 1.0]
MOVED_BALL = [0.5, -0.5, 2.0]


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


@pytest.mark.webots_world(SYNC, scope="function")
def test_reset_has_landed_when_it_returns(webots: WebotsSession, tmp_path: Path, wait_for_log: WaitForLog) -> None:
    hold = tmp_path / "hold"
    pacer = webots.launch_controller("probe", PACER, args=(str(hold),))

    field = webots.supervisor.getFromDef("BALL").getField("translation")
    field.setSFVec3f(MOVED_BALL)
    webots.step(32)
    assert field.getSFVec3f() == pytest.approx(MOVED_BALL)

    hold.touch()
    wait_for_log(pacer, "pacer holding")  # the robot stops stepping: the clock stands still

    webots.reset()
    observed = webots.supervisor.getFromDef("BALL").getField("translation").getSFVec3f()
    assert observed == pytest.approx(INITIAL_BALL)


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


@pytest.mark.webots_world(SYNC, scope="function")
@pytest.mark.webots_controller("probe", str(QUITTER))
def test_deliberate_quit_is_not_reported_as_a_crash(webots: WebotsSession, wait_for_log: WaitForLog) -> None:
    # A controller ending the simulation is a normal shutdown, so the test is
    # told the simulation was quit rather than sent hunting a crash.
    wait_for_log(webots.controllers["probe"], "simulationQuit(0)")
    with pytest.raises(WebotsQuitError, match="was quit") as excinfo:
        for _ in range(50):
            webots.step()
    assert not isinstance(excinfo.value, WebotsCrashedError)


@pytest.mark.webots_world(SYNC, scope="function")
@pytest.mark.webots_controller("probe", str(QUITTER), args=["7"])
def test_nonzero_quit_stays_a_crash_and_names_both_causes(webots: WebotsSession, wait_for_log: WaitForLog) -> None:
    wait_for_log(webots.controllers["probe"], "simulationQuit(7)")
    with pytest.raises(WebotsCrashedError, match=r"exited with code 7.*simulationQuit\(7\)"):
        for _ in range(50):
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


@pytest.mark.webots_world(SYNC)
@pytest.mark.webots_controller("probe", str(PROBE))
def test_pause_freezes_the_clock(webots: WebotsSession) -> None:
    webots.step()  # the controller is driving the clock
    webots.pause()
    frozen = webots.sim_time()
    # queries still answer while paused
    assert webots.supervisor.getFromDef("BALL").getPosition() == pytest.approx([0.0, 0.0, 1.0])
    time.sleep(0.4)  # real time passes; sim time must not
    assert webots.sim_time() == frozen
    with pytest.raises(WebotsError, match="paused"):
        webots.step()
    webots.play()
    webots.step(64)
    assert webots.sim_time() > frozen


_BOOTS_WHEN_PAUSED_TEST_RAN: list[int] = []


@pytest.mark.webots_world(SYNC)
@pytest.mark.webots_controller("probe", str(PROBE))
def test_a_test_may_end_while_paused(webots: WebotsSession, world_boots: list[str]) -> None:
    # Teardown's reset lands via a step, so it must resume first or hang.
    webots.pause()
    _BOOTS_WHEN_PAUSED_TEST_RAN.append(world_boots.count("sync.wbt"))


@pytest.mark.webots_world(SYNC)
@pytest.mark.webots_controller("probe", str(PROBE))
def test_world_survives_a_test_that_ended_paused(webots: WebotsSession, world_boots: list[str]) -> None:
    assert world_boots.count("sync.wbt") == _BOOTS_WHEN_PAUSED_TEST_RAN[0]  # reused, not rebooted
    assert not webots.paused
    webots.step()


@pytest.mark.webots_world(SYNC, scope="function")
@pytest.mark.webots_controller("probe", str(PROBE))
def test_play_to_walks_the_timeline(webots: WebotsSession) -> None:
    webots.pause()
    t0 = round(webots.sim_time() * 1000)
    landed = webots.play_to(t0 + 1000)
    assert t0 + 1000 <= landed <= t0 + 1000 + 64  # boundary plus at most the measured overshoot
    assert webots.paused
    time.sleep(0.3)  # real time passes; the timeline must not
    assert round(webots.sim_time() * 1000) == landed
    assert webots.play_to(t0 + 2000) >= t0 + 2000
    with pytest.raises(WebotsError, match="cannot rewind"):
        webots.play_to(t0 + 1000)


@pytest.mark.webots_world(SYNC, mode="pause", scope="function")
@pytest.mark.webots_controller("probe", str(PROBE))
def test_mode_pause_boots_frozen_at_zero(webots: WebotsSession) -> None:
    # Webots announces nothing while paused, so the plugin boots fast and
    # freezes via the agent while the empty sync seats still hold t=0.
    assert webots.paused
    assert webots.sim_time() == 0.0
    assert "probe" in webots.world.connected  # controllers connect while paused
    landed = webots.play_to(320)
    assert 320 <= landed <= 320 + 64
