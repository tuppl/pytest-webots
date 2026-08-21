import time
from collections.abc import Callable
from pathlib import Path

import pytest

from pytest_webots import ControllerProcess, ControllerSpec, WebotsError
from pytest_webots._core.config import Settings

MakeSettings = Callable[..., Settings]


class StubInstance:
    alive = True
    port = 1234
    world_path = "worlds/stub.wbt"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.robots = {"probe": "ipc://1234/probe"}

    def connection_generation(self, name: str) -> int:
        return 0

    def output(self) -> str:
        return ""


class ConnectThenExitInstance(StubInstance):
    """
    Reports a connect event on every poll after the snapshot, like a
    controller that connected and finished within one poll interval.
    """

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.calls = 0

    def connection_generation(self, name: str) -> int:
        self.calls += 1
        return 0 if self.calls == 1 else 1


def test_connect_timeout_terminates_the_process(tmp_path: Path, make_settings: MakeSettings) -> None:
    sleeper = tmp_path / "sleeper.py"
    sleeper.write_text("import time\ntime.sleep(60)\n")
    settings = make_settings(home=tmp_path, startup_timeout=1.0)
    process = ControllerProcess(
        ControllerSpec(robot="probe", path=sleeper),
        StubInstance(settings),  # type: ignore[arg-type]
    )
    start = time.monotonic()
    with pytest.raises(WebotsError, match="did not connect within"):
        process.start()
    assert time.monotonic() - start < 10
    assert not process.alive  # the spawned process must not outlive the failed attempt


def test_connect_then_immediate_exit_is_a_successful_start(tmp_path: Path, make_settings: MakeSettings) -> None:
    quick = tmp_path / "quick.py"
    quick.write_text("print('done')\n")
    settings = make_settings(home=tmp_path, startup_timeout=5.0)
    process = ControllerProcess(
        ControllerSpec(robot="probe", path=quick),
        ConnectThenExitInstance(settings),  # type: ignore[arg-type]
    )
    process.start()  # a connect event newer than the snapshot means success, even after exit
    deadline = time.monotonic() + 10
    while process.alive and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not process.alive


def test_exit_without_connect_still_fails_with_exit_code(tmp_path: Path, make_settings: MakeSettings) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("raise SystemExit(3)\n")
    settings = make_settings(home=tmp_path, startup_timeout=10.0)
    process = ControllerProcess(
        ControllerSpec(robot="probe", path=bad),
        StubInstance(settings),  # type: ignore[arg-type]
    )
    start = time.monotonic()
    with pytest.raises(WebotsError, match="exited with code 3 before connecting"):
        process.start()
    assert time.monotonic() - start < 5  # the post-exit log grace, not the full timeout
