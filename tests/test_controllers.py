import time
from pathlib import Path

import pytest

from pytest_webots import ControllerProcess, WebotsError, WebotsSession
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


def test_failure_report_shows_controller_output(pytester: pytest.Pytester) -> None:
    world = Path(__file__).parent / "worlds" / "second.wbt"
    pytester.makepyfile(
        f"""
        import pytest

        @pytest.mark.webots_world({str(world)!r})
        @pytest.mark.webots_controller("probe", {str(PROBE)!r})
        def test_fails(webots):
            assert False, "deliberate"
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "*webots output*",
            "*extern controller: connected*",
            "*webots controller 'probe'*",
            "*probe controller ready*",
        ]
    )
