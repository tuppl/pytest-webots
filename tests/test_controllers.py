import dataclasses
import shutil
import time
from pathlib import Path

import pytest

from pytest_webots import ControllerProcess, WebotsError, WebotsSession, fixture_ref
from pytest_webots._core.build import run_build
from pytest_webots._core.config import discover_webots_home

pytestmark = pytest.mark.skipif(discover_webots_home(None) is None, reason="no Webots installation found")

MINIMAL = "worlds/minimal.wbt"
PROBE = Path(__file__).parent / "controllers" / "probe"


def wait_for_log(process: ControllerProcess, needle: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if needle in process.logs:
            return
        time.sleep(0.05)
    raise AssertionError(f"{needle!r} not found in controller logs:\n{process.logs}")


@pytest.mark.webots_world(MINIMAL)
@pytest.mark.webots_controller("probe", "controllers/probe")
def test_marker_launches_controller(webots: WebotsSession) -> None:
    process = webots.controllers["probe"]
    assert process.alive
    wait_for_log(process, "probe controller ready")


@pytest.mark.webots_world(MINIMAL)
@pytest.mark.webots_controller("probe", "controllers/probe", args=["--role=striker"])
def test_controller_args_and_restart(webots: WebotsSession) -> None:
    process = webots.controllers["probe"]
    wait_for_log(process, "probe args: --role=striker")
    process.restart()
    assert process.alive
    wait_for_log(process, "probe controller ready")


@pytest.mark.webots_world(MINIMAL)
@pytest.mark.webots_controller("probe", "controllers/probe", protocol="tcp")
def test_tcp_controller(webots: WebotsSession) -> None:
    process = webots.controllers["probe"]
    assert process.alive
    assert process.controller_url().startswith("tcp://127.0.0.1:")
    wait_for_log(process, "probe controller ready")


@pytest.fixture
def probe_path() -> Path:
    return PROBE


@pytest.mark.webots_world(MINIMAL)
@pytest.mark.webots_controller("probe", fixture_ref("probe_path"))
def test_fixture_ref_path_launches_controller(webots: WebotsSession) -> None:
    process = webots.controllers["probe"]
    assert process.alive
    wait_for_log(process, "probe controller ready")


@pytest.fixture
def role_flag(role: str) -> str:
    return f"--role={role}"


@pytest.mark.parametrize("role", ["striker", "keeper"])
@pytest.mark.webots_world(MINIMAL)
@pytest.mark.webots_controller("probe", "controllers/probe", args=[fixture_ref("role_flag")])
def test_fixture_ref_follows_parametrize(webots: WebotsSession, role: str) -> None:
    wait_for_log(webots.controllers["probe"], f"probe args: --role={role}")


def test_fixture_ref_on_webots_dependent_fixture_is_diagnosed(pytester: pytest.Pytester) -> None:
    world = Path(__file__).parent / "worlds" / "second.wbt"
    pytester.makepyfile(
        f"""
        import pytest
        from pytest_webots import fixture_ref

        @pytest.fixture
        def needs_webots(webots):
            return "unreachable"

        @pytest.mark.webots_world({str(world)!r})
        @pytest.mark.webots_controller("probe", fixture_ref("needs_webots"))
        def test_circular(webots):
            pass
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*fixture_ref('needs_webots')*circular*launch_controller*"])


def test_fixture_ref_to_unknown_fixture_errors_clearly(pytester: pytest.Pytester) -> None:
    world = Path(__file__).parent / "worlds" / "second.wbt"
    pytester.makepyfile(
        f"""
        import pytest
        from pytest_webots import fixture_ref

        @pytest.mark.webots_world({str(world)!r})
        @pytest.mark.webots_controller("probe", fixture_ref("no_such_fixture"))
        def test_missing(webots):
            pass
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*fixture_ref('no_such_fixture')*no fixture named 'no_such_fixture'*"])


@pytest.mark.webots_world(MINIMAL)
def test_imperative_launch(webots: WebotsSession) -> None:
    process = webots.launch_controller("probe", PROBE / "probe.py")
    assert process.alive
    assert webots.controllers["probe"] is process
    wait_for_log(process, "probe controller ready")


@pytest.mark.webots_world(MINIMAL)
@pytest.mark.webots_controller("probe", "controllers/probe", autostart=False)
def test_autostart_false_launches_on_demand(webots: WebotsSession) -> None:
    assert "probe" not in webots.controllers
    process = webots.launch_controller("probe")
    assert process.alive


@pytest.mark.webots_world(MINIMAL)
def test_crashing_controller_fails_fast_with_logs(webots: WebotsSession, tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text('print("exploding now", flush=True)\nraise SystemExit(3)\n')
    start = time.monotonic()
    with pytest.raises(WebotsError, match="exited with code 3 before connecting") as excinfo:
        webots.launch_controller("probe", bad)
    assert time.monotonic() - start < 10  # fail fast, not the full startup timeout
    assert "exploding now" in str(excinfo.value)


@pytest.mark.skipif(shutil.which("make") is None, reason="make not available")
@pytest.mark.webots_world(MINIMAL)
@pytest.mark.webots_controller("probe", "controllers/cprobe")
def test_c_controller_builds_and_runs(webots: WebotsSession) -> None:
    process = webots.controllers["probe"]
    assert process.alive
    wait_for_log(process, "cprobe controller ready")
    settings = webots.world.settings
    assert run_build(process.spec, settings) is False
    assert run_build(process.spec, dataclasses.replace(settings, rebuild=True)) is True


def test_unknown_robot_name_fails_with_available(pytester: pytest.Pytester) -> None:
    world = Path(__file__).parent / "worlds" / "second.wbt"
    pytester.makepyfile(
        f"""
        import pytest

        @pytest.mark.webots_world({str(world)!r})
        @pytest.mark.webots_controller("ghost", {str(PROBE)!r})
        def test_ghost(webots):
            pass
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*no robot named 'ghost'*available:*probe*"])


def test_build_failure_is_contained_and_replayed(pytester: pytest.Pytester, tmp_path: Path) -> None:
    """
    A broken build fails its tests without touching the world: the next test
    reuses the still-running world, and later tests requiring the broken
    controller replay the cached failure instead of re-running the build.
    """
    world = Path(__file__).parent / "worlds" / "second.wbt"
    counter = tmp_path / "count.txt"
    broken = pytester.path / "broken_ctrl"
    broken.mkdir()
    (broken / "src.txt").write_text("v1")
    pytester.makepyfile(
        f"""
        import pytest

        BROKEN = dict(build=("sh", "-c", "echo x >> {counter}; echo kaput; exit 9"))

        @pytest.mark.webots_world({str(world)!r})
        @pytest.mark.webots_controller("probe", "broken_ctrl", **BROKEN)
        def test_broken_first(webots):
            pass

        @pytest.mark.webots_world({str(world)!r})
        @pytest.mark.webots_controller("probe", {str(PROBE / "probe.py")!r})
        def test_world_survives(webots):
            assert webots.controllers["probe"].alive

        @pytest.mark.webots_world({str(world)!r})
        @pytest.mark.webots_controller("probe", "broken_ctrl", **BROKEN)
        def test_broken_again(webots):
            pass
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1, errors=2)
    result.stdout.fnmatch_lines(["*kaput*", "*cached failure*"])
    assert "WebotsCrashedError" not in result.stdout.str()
    assert counter.read_text() == "x\n"  # the failing build ran exactly once


def test_launch_failure_leaves_the_world_reusable(pytester: pytest.Pytester, tmp_path: Path) -> None:
    """
    A marker controller that dies before connecting errors its own test, but the
    world survives: the failed launch is re-crewed and reset like any teardown,
    so the next test reuses the instance instead of paying for a boot.
    """
    world = Path(__file__).parent / "worlds" / "second.wbt"
    boots = tmp_path / "boots.txt"
    (pytester.path / "bad.py").write_text("raise SystemExit(3)\n")
    pytester.makeconftest(
        f"""
        def pytest_webots_world_started(instance):
            with open({str(boots)!r}, "a") as record:
                record.write(instance.spec.path.name + "\\n")
        """
    )
    pytester.makepyfile(
        f"""
        import pytest

        @pytest.mark.webots_world({str(world)!r})
        @pytest.mark.webots_controller("probe", "bad.py")
        def test_dead_controller(webots):
            pass

        @pytest.mark.webots_world({str(world)!r})
        def test_reuses_the_world(webots):
            assert webots.world.alive
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*exited with code 3 before connecting*"])
    assert boots.read_text() == "second.wbt\n"  # one boot: the failure did not cost the world


def test_failed_connect_leaves_world_usable(pytester: pytest.Pytester) -> None:
    """
    The downstream ~34s scenario: after a controller fails to connect, the
    world must still step and reset promptly instead of hanging into a
    misdiagnosed crash.
    """
    world = Path(__file__).parent / "worlds" / "second.wbt"
    (pytester.path / "sleeper.py").write_text("import time\ntime.sleep(60)\n")
    pytester.makepyfile(
        f"""
        import pytest
        from pytest_webots import WebotsError

        @pytest.mark.webots_world({str(world)!r})
        def test_survives_failed_connect(webots):
            with pytest.raises(WebotsError, match="did not connect"):
                webots.launch_controller("probe", "sleeper.py")
            webots.step()
        """
    )
    start = time.monotonic()
    result = pytester.runpytest("-p", "no:cacheprovider", "-o", "webots_startup_timeout=8")
    result.assert_outcomes(passed=1)
    assert "WebotsCrashedError" not in result.stdout.str()
    assert time.monotonic() - start < 60


def test_departed_sync_controller_is_recrewed_for_teardown(pytester: pytest.Pytester, tmp_path: Path) -> None:
    """
    A synchronization TRUE controller that exits mid-test must not stall the
    teardown reset: the robot is re-crewed with a stub, teardown stays fast,
    and the next test reuses the world instead of paying a reboot.
    """
    world = Path(__file__).parent / "worlds" / "sync.wbt"
    boots = tmp_path / "boots.txt"
    pytester.makeconftest(
        f"""
        def pytest_webots_world_started(instance):
            with open({str(boots)!r}, "a") as record:
                record.write(instance.spec.path.name + "\\n")
        """
    )
    (pytester.path / "departer.py").write_text(
        "from controller import Robot\n\nrobot = Robot()\nfor _ in range(3):\n    robot.step(32)\n"
    )
    pytester.makepyfile(
        f"""
        import time

        import pytest

        @pytest.mark.webots_world({str(world)!r})
        @pytest.mark.webots_controller("probe", "departer.py")
        def test_controller_departs(webots):
            process = webots.controllers["probe"]
            deadline = time.monotonic() + 10
            while process.alive and time.monotonic() < deadline:
                time.sleep(0.05)
            assert not process.alive

        @pytest.mark.webots_world({str(world)!r})
        @pytest.mark.webots_controller("probe", {str(PROBE / "probe.py")!r})
        def test_world_reused(webots):
            assert webots.controllers["probe"].alive
        """
    )
    start = time.monotonic()
    result = pytester.runpytest("-p", "no:cacheprovider")
    elapsed = time.monotonic() - start
    result.assert_outcomes(passed=2)
    assert "WebotsCrashedError" not in result.stdout.str()
    assert elapsed < 20  # the departed-robot stall alone was 30s
    assert boots.read_text() == "sync.wbt\n"  # reused, not rebooted


def test_simulation_quit_controller_does_not_stall_teardown(pytester: pytest.Pytester) -> None:
    world = Path(__file__).parent / "worlds" / "sync.wbt"
    (pytester.path / "quitter.py").write_text(
        "from controller import Supervisor\n\n"
        "robot = Supervisor()\n"
        "robot.step(32)\n"
        "robot.simulationQuit(0)\n"
        "robot.step(32)\n"
    )
    pytester.makepyfile(
        f"""
        import time

        import pytest

        @pytest.mark.webots_world({str(world)!r})
        @pytest.mark.webots_controller("probe", "quitter.py")
        def test_controller_quits(webots):
            process = webots.controllers["probe"]
            deadline = time.monotonic() + 10
            while process.alive and time.monotonic() < deadline:
                time.sleep(0.05)
            assert not process.alive

        @pytest.mark.webots_world({str(world)!r})
        @pytest.mark.webots_controller("probe", {str(PROBE / "probe.py")!r})
        def test_next_test_recovers(webots):
            assert webots.controllers["probe"].alive
        """
    )
    start = time.monotonic()
    result = pytester.runpytest("-p", "no:cacheprovider")
    elapsed = time.monotonic() - start
    result.assert_outcomes(passed=2)
    assert elapsed < 20
    assert "did not become ready" not in result.stdout.str()


@pytest.mark.skipif(shutil.which("make") is None, reason="make not available")
def test_build_hook_claims_custom_backend(pytester: pytest.Pytester, tmp_path: Path) -> None:
    world = Path(__file__).parent / "worlds" / "second.wbt"
    cprobe = Path(__file__).parent / "controllers" / "cprobe"
    witness = tmp_path / "hook-ran"
    home = discover_webots_home(None)
    pytester.makeconftest(
        f"""
        import os
        import subprocess
        from pathlib import Path

        from pytest_webots._core.config import make_home

        def pytest_webots_build_controller(spec, config):
            if spec.build == "my-backend":
                env = os.environ | {{
                    "WEBOTS_HOME": {str(home)!r},
                    "WEBOTS_HOME_PATH": str(make_home(Path({str(home)!r}))),
                }}
                subprocess.run(["make", "-C", str(spec.path.parent)], check=True, env=env)
                Path({str(witness)!r}).touch()
                return True
        """
    )
    pytester.makepyfile(
        f"""
        import pytest

        @pytest.mark.webots_world({str(world)!r})
        @pytest.mark.webots_controller("probe", {str(cprobe)!r}, build="my-backend")
        def test_hook_built(webots):
            assert webots.controllers["probe"].alive
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1)
    assert witness.exists()
