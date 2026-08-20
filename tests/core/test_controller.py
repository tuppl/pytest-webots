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

    def connection_active(self, name: str, after: int) -> bool:
        return False

    def output(self) -> str:
        return ""


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
